"""Do not combine a bonus payment with an independently priced checkout or receipt."""
from sqlalchemy import select

from app.api.core.cashback_wallets import CashbackError
from app.api.core import visit_settlement_guard as settlements
from app.db.models.service_payment import ServicePaymentAttempt


def ensure_spend_allowed(db, visit_id, business_id):
    # The caller already owns Business/Client/Visit anchors. Attempt-first payment
    # writers must get a retry here, not a Visit-to-Attempt blocking cycle.
    try:
        reason = settlements.settlement_reason(db, visit_id, business_id)
    except (settlements.SettlementBusy, settlements.DirtySettlementState):
        raise CashbackError('visit_price_retry') from None
    except settlements.SettlementTargetUnavailable:
        raise CashbackError('visit_not_found') from None
    if reason == 'visit_price_payment_pending':
        raise CashbackError('loyalty_payment_checkout_exists')
    if reason:
        raise CashbackError('loyalty_payment_settlement_required')
    with db.no_autoflush:
        # Even a closed attempt can receive a late provider event. A new bonus
        # payment cannot silently re-price that historical external checkout.
        if db.execute(select(ServicePaymentAttempt.id).where(
                ServicePaymentAttempt.visit_id == visit_id).limit(1)).first() is not None:
            raise CashbackError('loyalty_payment_checkout_exists')
