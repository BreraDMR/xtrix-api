"""Explicit quote replacement with historical settlement guards and mandatory audit."""
from dataclasses import replace
import hmac

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.api.core import audit_service, crm_metering, visit_price_access, visit_price_quote
from app.api.core import visit_settlement_guard as guards
from app.db.models.visit import Visit
from app.db.models.visit_item import VisitItem
from app.schemas.visit_price import VisitPriceChange


class QuoteChanged(ValueError):
    """The caller must read a new quote before attempting another replacement."""


class QuoteItemsInvalid(ValueError):
    """Every stored item must be priced exactly once, without replacing its identity."""


def reprice_visit(db: Session, authorized_visit: Visit, change: VisitPriceChange,
                  *, actor=None, request_id=None) -> visit_price_quote.VisitPriceQuote:
    """Stage one explicit quote. The caller commits or rolls back the whole action.

    No FX, provider call, receipt rewrite or notification is performed. Pending
    caller work is rejected before audit's own flush, rather than flushed silently.
    """
    with db.no_autoflush:
        if db.new or db.dirty or db.deleted:
            raise guards.DirtySettlementState('Save pending changes before replacing a quote')
        crm_metering.enforce_visit_quota(db, authorized_visit.business_id, authorized_visit.specialist_id,
            authorized_visit.date_start, authorized_visit.status, exclude_id=authorized_visit.id)
        target = guards.lock_quote_update_target(db, authorized_visit, actor_id=getattr(actor, 'id', None))
        state = visit_price_access.read_stored_price(db, target)
        if state is None:
            raise guards.SettlementTargetUnavailable('Stored quote target is not available')
        if not hmac.compare_digest(visit_price_quote.fingerprint(state), change.expected_quote):
            raise QuoteChanged('The stored quote changed')
        try:
            visit_price_quote.validate_change_items(state, change)
        except ValueError:
            raise QuoteItemsInvalid('Price every stored item exactly once') from None
        prices = {item.item_id: item.price for item in change.items} if change.items is not None else {}
        desired = replace(state, currency_snapshot=change.currency, total=change.total,
            items=tuple(replace(item, price=prices[item.id]) for item in state.items))
        if visit_price_quote.fingerprint(desired) == visit_price_quote.fingerprint(state):
            return visit_price_quote.project_price(state)
        guards.ensure_price_change_allowed(db, target.id, target.business_id)
        if (target.status == 'completed' and change.total > 0
                and target.payment_method not in {'cash', 'card', 'other'}):
            raise guards.VisitPriceChangeBlocked('payment_method_required_for_completed_visits')
        changed = db.execute(update(Visit).where(Visit.id == target.id,
            Visit.business_id == target.business_id, Visit.client_id == target.client_id,
            Visit.specialist_id == target.specialist_id, Visit.deleted_at.is_(None)
        ).values(price=float(change.total), currency_snapshot=change.currency).execution_options(synchronize_session=False))
        if changed.rowcount != 1:
            raise guards.SettlementTargetUnavailable('Stored quote target is not available')
        for item in state.items:
            changed = db.execute(update(VisitItem).where(VisitItem.id == item.id,
                VisitItem.visit_id == target.id).values(price=prices[item.id]).execution_options(synchronize_session=False))
            if changed.rowcount != 1:
                raise QuoteItemsInvalid('The stored item is no longer available')
        saved = visit_price_access.read_stored_price(db, target)
        if saved is None or visit_price_quote.fingerprint(saved) != visit_price_quote.fingerprint(desired):
            raise QuoteChanged('The saved quote does not match the explicit replacement')
        event = audit_service.record_event(db, action='visit.repriced', entity_type='visit', entity_id=target.id,
            business_id=target.business_id, owner_user_id=target.specialist_id if target.business_id is None else None,
            actor=actor, request_id=request_id, changes={
                'fields': ['price', 'currency_snapshot', 'items'],
                'price': {'old': state.total, 'new': saved.total},
                'currency_snapshot': {'old': state.currency_snapshot, 'new': saved.currency_snapshot},
                'items_count': len(saved.items),
            })
        if event is None:
            raise guards.VisitPriceChangeBlocked('visit_price_retry')
        return visit_price_quote.project_price(saved)
