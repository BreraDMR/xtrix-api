"""Capture the unit of new visit prices without guessing units for older amounts."""
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.db.models.business import Business
from app.db.models.service import Service
from app.db.models.user import User
from app.schemas.limits import ISO_4217


def lock_user_references(db: Session, user_ids) -> None:
    """Take future FK key locks before Business SHARE, in a stable order.

    This does not freeze staff membership or replace the caller's permission checks.
    """
    ids = sorted({value for value in user_ids if isinstance(value, int) and not isinstance(value, bool) and value > 0})
    if ids:
        with db.no_autoflush:
            db.query(User.id).filter(User.id.in_(ids)).order_by(User.id).with_for_update(
                read=True, key_share=True).all()


def lock_business_currency(db: Session, business_id: int | None) -> str | None:
    """Read a scalar under SHARE; caller keeps the lock until commit or rollback.

    Acquire the caller's payer/User locks first. Neither cached ORM state nor a
    default unit can prove the currency of a newly saved price.
    """
    if business_id is None:
        return None
    # A dirty caller-owned Business will be written by the normal final flush.
    # Lock it strongly now, rather than upgrading our SHARE after other readers arrive.
    pending = next((row for row in db.dirty if isinstance(row, Business)
                    and inspect(row).identity == (business_id,)
                    and db.is_modified(row, include_collections=False)), None)
    with db.no_autoflush:
        current = db.query(Business.currency).filter(Business.id == business_id).with_for_update(
            read=pending is None).one_or_none()
    if current is None:
        return None
    currency = (pending.currency if pending is not None
                and inspect(pending).attrs.currency.history.has_changes() else current.currency)
    return currency if isinstance(currency, str) and currency in ISO_4217 else None


def fresh_catalog(db: Session, service_ids) -> dict[int, Service]:
    """Refresh catalog facts without erasing the caller's pending column edits.

    Caller already holds the target Business lock. No early Service UPDATE is issued,
    so pending catalog changes keep the normal Business -> Service write order.
    """
    ids = set(service_ids)
    pending = {inspect(row).identity[0]: row for row in db.dirty
               if isinstance(row, Service) and inspect(row).identity
               and inspect(row).identity[0] in ids}
    with db.no_autoflush:
        result = {row.id: row for row in db.query(Service).filter(
            Service.id.in_(ids - pending.keys())).populate_existing().all()} if ids - pending.keys() else {}
        # A missing persisted row must not be resurrected from the identity map.
        existing = {row.id for row in db.query(Service.id).filter(Service.id.in_(pending)).all()} if pending else set()
        for service_id in existing:
            row = pending[service_id]
            state = inspect(row)
            if state.attrs.id.history.has_changes():
                raise ValueError('Save catalog primary-key changes before creating a visit')
            unchanged = [prop.key for prop in state.mapper.column_attrs
                         if not state.attrs[prop.key].history.has_changes()]
            db.refresh(row, attribute_names=unchanged)
            result[service_id] = row
    return result


def currency_for_catalog(currency: str | None, business_id: int | None, services) -> str | None:
    """A foreign or legacy unscoped catalog cannot prove the target visit's unit.

    Some internal callers copy a catalog price into the apparent manual-price field,
    so a selected foreign service stays unknown even with a nonzero top-level price.
    """
    if currency is None or any(service.business_id != business_id for service in services):
        return None
    return currency
