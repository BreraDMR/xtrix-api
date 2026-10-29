"""Recorded settlement facts, independent of a visit's current balance or status.

Caller acquires scheduling anchors before this helper and rolls back the entire
transaction on refusal. No provider, refund, commit or implicit flush happens here.
"""
from sqlalchemy import exists, inspect, or_, select, func
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.db.models.gift import GiftLedgerEntry
from app.db.models.loyalty_program import LoyaltyTransaction
from app.db.models.service_payment import ServicePaymentAttempt, ServicePaymentEvent, ServicePaymentRefund
from app.db.models.visit import Visit


class SettlementBusy(ValueError):
    """Retry only after whole transaction rollback."""


class DirtySettlementState(ValueError):
    """Save pending financial changes before checking a stored quote."""


class SettlementTargetUnavailable(ValueError):
    """The caller's authorized target is no longer present in that scope."""


class VisitPriceChangeBlocked(ValueError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


_FINANCIAL = (ServicePaymentAttempt, ServicePaymentEvent, ServicePaymentRefund, GiftLedgerEntry, LoyaltyTransaction)


def settlement_reason(db: Session, visit_id: int, business_id: int | None) -> str | None:
    """Visit NOWAIT -> every existing attempt NOWAIT -> current scalar evidence.

    All attempts participate, including closed ones that can still receive a late
    paid event. Reversals never subtract from the historical fact of a settlement.
    This is not CRM authorization; the caller supplies its already authorized scope.
    """
    try:
        with db.no_autoflush:
            for row in db.new.union(db.dirty).union(db.deleted):
                if isinstance(row, _FINANCIAL) or (isinstance(row, Visit)
                    and (inspect(row).identity == (visit_id,) or row.id == visit_id)):
                    raise DirtySettlementState('Save financial changes before changing a stored quote')
            visit = db.execute(select(Visit.client_id, Visit.manual_payment_recorded_at).where(
                Visit.id == visit_id, Visit.business_id == business_id, Visit.deleted_at.is_(None)
                ).with_for_update(nowait=True)).one_or_none()
            if visit is None:
                raise SettlementTargetUnavailable('Stored quote target is not available')
            db.execute(select(ServicePaymentAttempt.id).where(ServicePaymentAttempt.visit_id == visit_id)
                .order_by(ServicePaymentAttempt.id).with_for_update(nowait=True)).all()
            attempt_ids = select(ServicePaymentAttempt.id).where(ServicePaymentAttempt.visit_id == visit_id)
            paid = exists(select(1).where(ServicePaymentAttempt.visit_id == visit_id, or_(
                ServicePaymentAttempt.status.in_(('paid', 'partially_refunded', 'refunded', 'reconcile')),
                ServicePaymentAttempt.paid_at.is_not(None), ServicePaymentAttempt.refunded_amount > 0,
                ServicePaymentAttempt.business_id.is_distinct_from(business_id))))
            events = exists(select(1).where(ServicePaymentEvent.attempt_id.in_(attempt_ids), or_(
                ServicePaymentEvent.event_type.in_(('payment.paid', 'refund.succeeded')),
                ServicePaymentEvent.business_id.is_distinct_from(business_id))))
            refunds = exists(select(1).where(ServicePaymentRefund.attempt_id.in_(attempt_ids)))
            gifts = exists(select(1).where(GiftLedgerEntry.visit_id == visit_id, or_(
                GiftLedgerEntry.entry_type == 'redeemed', GiftLedgerEntry.business_id.is_distinct_from(business_id))))
            loyalty = exists(select(1).where(LoyaltyTransaction.visit_id == visit_id, or_(
                (LoyaltyTransaction.transaction_type.in_(('earned', 'spent')) & (
                    (LoyaltyTransaction.points_amount != 0) | (func.coalesce(LoyaltyTransaction.cashback_amount, 0) != 0))),
                LoyaltyTransaction.business_id.is_distinct_from(business_id),
                LoyaltyTransaction.client_id.is_distinct_from(visit.client_id))))
            settled, pending = db.execute(select(or_(paid, events, refunds, gifts, loyalty),
                exists(select(1).where(ServicePaymentAttempt.visit_id == visit_id,
                                      ServicePaymentAttempt.status == 'pending')))).one()
            if visit.manual_payment_recorded_at is not None or settled:
                return 'visit_price_settlement_required'
            return 'visit_price_payment_pending' if pending else None
    except DBAPIError as error:
        code = getattr(error.orig, 'pgcode', None) or getattr(error.orig, 'sqlstate', None)
        if code in {'55P03', '40P01'}:
            raise SettlementBusy('Stored quote is busy; retry after rollback') from None
        raise


def ensure_price_change_allowed(db: Session, visit_id: int, business_id: int | None) -> None:
    reason = settlement_reason(db, visit_id, business_id)
    if reason:
        raise VisitPriceChangeBlocked(reason)


def lock_quote_update_target(db: Session, visit: Visit, proposed_master_ids=(), actor_id=None) -> Visit:
    """Acquire FK/slot anchors before Visit, and refresh only a clean target.

    Quota must already have been checked. A changed staff hint is retried rather
    than acquiring additional scheduling anchors after the visit row lock.
    """
    from app.db.models.business import Business
    from app.db.models.user import User
    from app.db.models.visit_item import VisitItem
    try:
        with db.no_autoflush:
            visit_id, business_id, client_id = visit.id, visit.business_id, visit.client_id
            quota_hint = (visit.specialist_id, visit.status, visit.date_start)
            for pending in db.new.union(db.dirty).union(db.deleted):
                if ((isinstance(pending, Visit) and pending.id == visit_id)
                        or isinstance(pending, VisitItem)):
                    raise DirtySettlementState('Save booking edits before applying another update')
            stored = set(db.execute(select(VisitItem.specialist_id).where(
                VisitItem.visit_id == visit_id)).scalars()) | {visit.specialist_id}
            masters = {value for value in stored | set(proposed_master_ids) | {actor_id}
                       if isinstance(value, int) and not isinstance(value, bool) and value > 0}
            if masters:
                db.execute(select(User.id).where(User.id.in_(masters)).order_by(User.id).with_for_update(
                    read=True, key_share=True, nowait=True)).all()
            from sqlalchemy import text
            for master_id in sorted(masters):
                if not db.execute(text('SELECT pg_try_advisory_xact_lock(hashtextextended(:key,0))'),
                    {'key': f'visit-slot:{master_id}'}).scalar_one():
                    raise SettlementBusy('Booking slot is busy; retry after rollback')
            if business_id is not None:
                business = db.execute(select(Business.id).where(Business.id == business_id,
                    Business.deleted_at.is_(None)).with_for_update(read=True, nowait=True)).scalar_one_or_none()
                if business is None:
                    raise SettlementTargetUnavailable('Stored quote target is not available')
            fresh = db.query(Visit).filter(Visit.id == visit_id, Visit.business_id == business_id,
                Visit.client_id == client_id, Visit.deleted_at.is_(None)).populate_existing().with_for_update(
                    nowait=True).one_or_none()
            if fresh is None:
                raise SettlementTargetUnavailable('Stored quote target is not available')
            if (fresh.specialist_id, fresh.status, fresh.date_start) != quota_hint:
                raise SettlementBusy('Booking changed after quota check; retry after rollback')
            db.query(VisitItem).filter(VisitItem.visit_id == visit_id).order_by(VisitItem.id
                ).populate_existing().with_for_update(nowait=True).all()
            db.expire(fresh, ['items'])
            if {fresh.specialist_id, *(item.specialist_id for item in fresh.items)} - masters:
                raise SettlementBusy('Booking staff changed; retry after rollback')
            return fresh
    except DBAPIError as error:
        code = getattr(error.orig, 'pgcode', None) or getattr(error.orig, 'sqlstate', None)
        if code in {'55P03', '40P01'}:
            raise SettlementBusy('Stored quote is busy; retry after rollback') from None
        raise
