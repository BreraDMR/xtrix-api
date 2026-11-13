"""Correct the price/currency of a visit that already has money or bonuses on it.

Damir's rule (01.11/13.11.2026): the old payment stays exactly as it was, in its own
currency. The visit gets the new quote, and the correction row remembers what the
quote and the recorded settlement looked like right before. Refunding the old money
is a separate step - here we only write down whether a refund is due. No FX, no
provider call, no receipt rewrite, nothing sent.
"""
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
import hashlib
import hmac
import json

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.core import audit_service, crm_metering, visit_price_access, visit_price_quote, visit_reprice
from app.api.core import visit_settlement_guard as guards
from app.api.core.clock import utcnow
from app.api.core.visit_settlement_projection import SettlementScope, read_settlement
from app.db.models.visit import Visit
from app.db.models.visit_price_correction import VisitPriceCorrection
from app.schemas.visit_price import VisitPriceCorrectionIn


class CorrectionNotNeeded(ValueError):
    """Nothing is recorded on the visit yet - the plain price editor is the right tool."""


class CorrectionUnchanged(ValueError):
    """The requested quote is the stored quote; a correction must change something."""


class CorrectionKeyConflict(ValueError):
    """The idempotency key was already used for a different correction of this visit."""


class CorrectionUnavailable(ValueError):
    """Visits outside a business have no settlement scope to correct."""


@dataclass(frozen=True)
class CorrectionResult:
    correction: VisitPriceCorrection
    quote: visit_price_quote.VisitPriceQuote
    replayed: bool


def _money(value):
    # same scale as the NUMERIC(12,2) column, so a fresh row and a replayed one read alike
    return None if value is None else Decimal(str(value)).quantize(Decimal('0.01'))


def _text(value):
    return None if value is None else format(_money(value), 'f')


def request_hash(change: VisitPriceCorrectionIn) -> str:
    """Stable fingerprint of what the caller asked for, so a retry can be told from a new request."""
    body = {
        'currency': change.currency,
        'expected_quote': change.expected_quote,
        'items': sorted((item.item_id, _text(item.price)) for item in change.items) if change.items is not None else None,
        'legacy_price': _text(change.legacy_price),
        'prior_payment_action': change.prior_payment_action,
        'reason': change.reason,
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _items_json(quote):
    return [{'item_id': item.id, 'position': item.position, 'service_id': item.service_id,
             'specialist_id': item.specialist_id, 'price': _text(item.price)} for item in quote.items]


def _facts_json(facts):
    # settled numbers go in as exact strings, JSON floats would lose cents
    out = {}
    for key, value in asdict(facts).items():
        if isinstance(value, Decimal):
            value = format(value, 'f')
        elif isinstance(value, tuple):
            value = [asdict(part) if hasattr(part, '__dataclass_fields__') else part for part in value]
            value = json.loads(json.dumps(value, default=lambda v: format(v, 'f') if isinstance(v, Decimal) else str(v)))
        out[key] = value
    return out


def _existing(db, visit_id, key):
    return db.execute(select(VisitPriceCorrection).where(
        VisitPriceCorrection.visit_id == visit_id, VisitPriceCorrection.idempotency_key == key)).scalar_one_or_none()


def _replay(db, target, row, digest):
    if not hmac.compare_digest(row.request_hash, digest):
        raise CorrectionKeyConflict('This key was used for a different correction')
    state = visit_price_access.read_stored_price(db, target)
    if state is None:
        raise guards.SettlementTargetUnavailable('Stored quote target is not available')
    return CorrectionResult(row, visit_price_quote.project_price(state), True)


def correct_visit_price(db: Session, authorized_visit: Visit, change: VisitPriceCorrectionIn,
                        *, actor=None, request_id=None) -> CorrectionResult:
    """Stage one correction. The caller commits or rolls back the whole thing."""
    if authorized_visit.business_id is None:
        raise CorrectionUnavailable('Only business visits have a settlement to correct')
    digest = request_hash(change)
    with db.no_autoflush:
        if db.new or db.dirty or db.deleted:
            raise guards.DirtySettlementState('Save pending changes before correcting a quote')
        # a retry after a lost response must not need the (already replaced) old fingerprint
        saved_row = _existing(db, authorized_visit.id, change.idempotency_key)
        if saved_row is not None:
            return _replay(db, authorized_visit, saved_row, digest)
        crm_metering.enforce_visit_quota(db, authorized_visit.business_id, authorized_visit.specialist_id,
            authorized_visit.date_start, authorized_visit.status, exclude_id=authorized_visit.id)
        target = guards.lock_quote_update_target(db, authorized_visit, actor_id=getattr(actor, 'id', None))
        # someone may have finished the same request while we waited for the locks
        saved_row = _existing(db, target.id, change.idempotency_key)
        if saved_row is not None:
            return _replay(db, target, saved_row, digest)
        state = visit_price_access.read_stored_price(db, target)
        if state is None:
            raise guards.SettlementTargetUnavailable('Stored quote target is not available')
        if not hmac.compare_digest(visit_price_quote.fingerprint(state), change.expected_quote):
            raise visit_reprice.QuoteChanged('The stored quote changed')
        try:
            visit_price_quote.validate_change_items(state, change)
        except ValueError:
            raise visit_reprice.QuoteItemsInvalid('Price every stored item exactly once') from None
        desired = visit_reprice.desired_quote(state, change)
        if visit_price_quote.fingerprint(desired) == visit_price_quote.fingerprint(state):
            raise CorrectionUnchanged('The requested quote is already stored')
        reason = guards.settlement_reason(db, target.id, target.business_id)
        if reason is None:
            raise CorrectionNotNeeded('Nothing is recorded yet; use the plain price editor')
        if reason == 'visit_price_payment_pending':
            raise guards.VisitPriceChangeBlocked(reason)
        facts = read_settlement(db, SettlementScope(target.id, target.business_id, target.client_id, target.specialist_id))
        if facts is None:
            raise guards.SettlementTargetUnavailable('Stored quote target is not available')
        if facts.has_pending_checkout:
            raise guards.VisitPriceChangeBlocked('visit_price_payment_pending')
        saved = visit_reprice.write_quote(db, target, state, change, desired)
        now: datetime = utcnow()
        row = VisitPriceCorrection(business_id=target.business_id, visit_id=target.id,
            actor_user_id=getattr(actor, 'id', None), idempotency_key=change.idempotency_key,
            request_hash=digest, old_currency=state.currency_snapshot,
            old_total=_money(state.total),
            old_items=_items_json(state), new_currency=saved.currency_snapshot,
            new_total=_money(saved.total), new_items=_items_json(saved), settlement=_facts_json(facts),
            prior_payment_action=change.prior_payment_action, reason=change.reason, created_at=now)
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError:
            # same key raced in from another request; the caller rolls back and retries into the replay
            raise guards.SettlementBusy('Correction is being saved; retry after rollback') from None
        event = audit_service.record_event(db, action='visit.price_corrected', entity_type='visit', entity_id=target.id,
            business_id=target.business_id, actor=actor, request_id=request_id, changes={
                'fields': ['price', 'currency_snapshot', 'items'],
                'price': {'old': state.total, 'new': saved.total},
                'currency_snapshot': {'old': state.currency_snapshot, 'new': saved.currency_snapshot},
                'items_count': len(saved.items),
                'correction_id': row.id,
                'prior_payment_action': change.prior_payment_action,
            })
        if event is None:
            raise guards.VisitPriceChangeBlocked('visit_price_retry')
        return CorrectionResult(row, visit_price_quote.project_price(saved), False)


def list_corrections(db: Session, authorized_visit: Visit):
    return db.execute(select(VisitPriceCorrection).where(
        VisitPriceCorrection.visit_id == authorized_visit.id,
        VisitPriceCorrection.business_id == authorized_visit.business_id,
    ).order_by(VisitPriceCorrection.id)).scalars().all()
