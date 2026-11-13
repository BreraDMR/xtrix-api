"""Write down an explicit charge, or a manual receipt/refund with its exact amount.

Rules (design 01.11, plan 13.11.2026): the entry is in the currency of the visit's
current quote and nothing else - no FX, no guessing from the salon currency. The old
"paid manually" marker has no amount and stays exactly as it is; we never turn it into
a number. Rows are append-only, a mistake is fixed with another entry. Nothing here
touches a provider, sends anything or commits - the caller owns the transaction.
"""
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import hmac
import json

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.api.core import audit_service
from app.api.core import visit_settlement_guard as guards
from app.api.core.clock import utcnow
from app.db.models.business import Business
from app.db.models.visit import Visit
from app.db.models.visit_settlement_entry import VisitSettlementEntry
from app.schemas.visit_settlement import VisitSettlementEntryIn


class EntryKeyConflict(ValueError):
    """The key was already used for a different entry on this visit."""


class EntryUnavailable(ValueError):
    """Visits outside a business have no settlement to write to."""


class EntryCurrencyMismatch(ValueError):
    """The entry currency is not the currency of the visit's current quote."""


class EntryCurrencyUnknown(ValueError):
    """The visit has no saved currency yet, so no amount can be tied to it."""


@dataclass(frozen=True)
class EntryResult:
    entry: VisitSettlementEntry
    replayed: bool


def request_hash(entry: VisitSettlementEntryIn) -> str:
    body = {
        'kind': entry.kind,
        # 10, 10.0 and "10.00" are the same money, so they hash the same
        'amount': format(entry.amount.quantize(Decimal('0.01')), 'f'),
        'currency': entry.currency,
        'reason': entry.reason,
        'reference': entry.reference,
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _existing(db, visit_id, key):
    return db.execute(select(VisitSettlementEntry).where(
        VisitSettlementEntry.visit_id == visit_id, VisitSettlementEntry.idempotency_key == key)).scalar_one_or_none()


def _replay(row, digest):
    if not hmac.compare_digest(row.request_hash, digest):
        raise EntryKeyConflict('This key was used for a different entry')
    return EntryResult(row, True)


def _lock_visit(db, visit: Visit):
    try:
        business = db.execute(select(Business.id).where(Business.id == visit.business_id,
            Business.deleted_at.is_(None)).with_for_update(read=True, nowait=True)).scalar_one_or_none()
        if business is None:
            raise guards.SettlementTargetUnavailable('Visit is not available')
        row = db.execute(select(Visit.id, Visit.currency_snapshot).where(
            Visit.id == visit.id, Visit.business_id == visit.business_id, Visit.deleted_at.is_(None)
            ).with_for_update(nowait=True)).one_or_none()
    except DBAPIError as error:
        code = getattr(error.orig, 'pgcode', None) or getattr(error.orig, 'sqlstate', None)
        if code in {'55P03', '40P01'}:
            raise guards.SettlementBusy('Visit is busy; retry after rollback') from None
        raise
    if row is None:
        raise guards.SettlementTargetUnavailable('Visit is not available')
    return row


def record_entry(db: Session, authorized_visit: Visit, entry: VisitSettlementEntryIn,
                 *, actor=None, request_id=None) -> EntryResult:
    """Stage one entry. The caller commits, or rolls back on any refusal."""
    if authorized_visit.business_id is None:
        raise EntryUnavailable('Only business visits have a settlement')
    digest = request_hash(entry)
    with db.no_autoflush:
        if db.new or db.dirty or db.deleted:
            raise guards.DirtySettlementState('Save pending changes before recording money')
        # a retry after a lost response gets the saved row, even if the quote moved on since
        saved = _existing(db, authorized_visit.id, entry.idempotency_key)
        if saved is not None:
            return _replay(saved, digest)
        locked = _lock_visit(db, authorized_visit)
        saved = _existing(db, locked.id, entry.idempotency_key)
        if saved is not None:
            return _replay(saved, digest)
        if locked.currency_snapshot is None:
            raise EntryCurrencyUnknown('Visit has no saved currency')
        if locked.currency_snapshot != entry.currency:
            raise EntryCurrencyMismatch('Entry currency differs from the visit quote')
        row = VisitSettlementEntry(business_id=authorized_visit.business_id, visit_id=locked.id,
            actor_user_id=getattr(actor, 'id', None), kind=entry.kind, amount=entry.amount,
            currency=entry.currency, reason=entry.reason, reference=entry.reference,
            idempotency_key=entry.idempotency_key, request_hash=digest, created_at=utcnow())
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError:
            # same key raced in from another request; rollback and the retry replays it
            raise guards.SettlementBusy('Entry is being saved; retry after rollback') from None
        event = audit_service.record_event(db, action='visit.settlement_entry_recorded', entity_type='visit',
            entity_id=locked.id, business_id=authorized_visit.business_id, actor=actor, request_id=request_id,
            changes={'entry_id': row.id, 'kind': entry.kind, 'amount': entry.amount, 'currency': entry.currency})
        if event is None:
            raise guards.VisitPriceChangeBlocked('visit_price_retry')
        db.refresh(row)
        return EntryResult(row, False)


def list_entries(db: Session, authorized_visit: Visit):
    return db.execute(select(VisitSettlementEntry).where(
        VisitSettlementEntry.visit_id == authorized_visit.id,
        VisitSettlementEntry.business_id == authorized_visit.business_id,
    ).order_by(VisitSettlementEntry.id)).scalars().all()
