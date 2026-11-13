"""Owner-stated currency for old visits that were saved before visits kept their own unit.

Nothing guesses here: the owner looks at the count, says "those were CZK", and only
those exact rows get the unit, with an audit entry. Rows that already have a unit are
never touched, and a count that changed in between is refused.
"""
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.core import audit_service
from app.db.models.business import Business
from app.db.models.visit import Visit


class LegacyCurrencyBusy(ValueError):
    pass


class LegacyCurrencyCountChanged(ValueError):
    pass


class LegacyCurrencyNothingToConfirm(ValueError):
    pass


def preview(db: Session, business_id: int) -> dict:
    row = db.execute(select(func.count(Visit.id), func.min(Visit.date_start), func.max(Visit.date_start)).where(
        Visit.business_id == business_id, Visit.currency_snapshot.is_(None))).one()
    currency = db.execute(select(Business.currency).where(Business.id == business_id)).scalar_one_or_none()
    return {'unknown_visits': row[0], 'first_visit_at': row[1], 'last_visit_at': row[2],
            'business_currency': currency}


def confirm(db: Session, business_id: int, currency: str, expected_count: int, *, actor=None, request_id=None) -> int:
    """Stage the update; the caller commits or rolls back."""
    try:
        # same anchor the quote writers share, so no visit changes unit while we label
        db.execute(select(Business.id).where(Business.id == business_id).with_for_update(nowait=True)).one()
        ids = db.execute(select(Visit.id).where(Visit.business_id == business_id, Visit.currency_snapshot.is_(None))
                         .order_by(Visit.id).with_for_update(nowait=True)).scalars().all()
    except DBAPIError as error:
        code = getattr(error.orig, 'pgcode', None)
        if code in {'55P03', '40P01'}:
            raise LegacyCurrencyBusy('Visits are being changed; retry') from None
        raise
    if not ids:
        raise LegacyCurrencyNothingToConfirm('No visits without a currency')
    if len(ids) != expected_count:
        raise LegacyCurrencyCountChanged('The number of visits changed since the preview')
    changed = db.execute(update(Visit).where(Visit.id.in_(ids), Visit.currency_snapshot.is_(None))
                         .values(currency_snapshot=currency).execution_options(synchronize_session=False))
    if changed.rowcount != len(ids):
        raise LegacyCurrencyCountChanged('The number of visits changed since the preview')
    event = audit_service.record_event(db, action='business.legacy_visit_currency_confirmed', entity_type='business',
        entity_id=business_id, business_id=business_id, actor=actor, request_id=request_id,
        changes={'currency': currency, 'updated_visits': len(ids)})
    if event is None:
        raise LegacyCurrencyBusy('Audit entry could not be saved; retry')
    return len(ids)
