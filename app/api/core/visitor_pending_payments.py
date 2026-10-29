"""Release unpaid holds in the visitor command's transaction, without provider calls."""
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.core import visitor_action_target as targets
from app.db.models.service_payment import ServicePaymentAttempt


class PendingPaymentConflict(ValueError):
    """An inconsistent payment target needs a separate review, not a scope fallback."""


def cancel_pending_payments(db: Session, raw_session: str | None,
                            target: targets.LockedActionTarget) -> tuple[int, ...]:
    """Mark every pending attempt cancelled while preserving its financial snapshot.

    The caller owns the locked visit, booking/billing policy and whole commit or
    rollback. Attempt-first payment writers may be waiting for that visit: taking
    a blocking attempt lock here would complete a deadlock cycle, so use NOWAIT.
    Paid, refunded and other closed attempts are unchanged. This does not refund
    or contact a provider; late provider events use the existing reconcile flow.
    """
    try:
        with db.no_autoflush:
            fresh = targets.revalidate_action_target(db, raw_session, target)
            if any(isinstance(row, ServicePaymentAttempt)
                   for row in db.new.union(db.dirty).union(db.deleted)):
                raise targets.DirtyActionState('Stage payment changes after locking their target')
            rows = db.execute(select(ServicePaymentAttempt.id, ServicePaymentAttempt.business_id).where(
                ServicePaymentAttempt.visit_id == fresh.visit_id,
                ServicePaymentAttempt.status == 'pending',
            ).order_by(ServicePaymentAttempt.id).with_for_update(nowait=True)).all()
            if any(row.business_id != fresh.business_id for row in rows):
                raise PendingPaymentConflict('Pending payment target is not available')
            fresh = targets.revalidate_action_target(db, raw_session, fresh)
            ids = tuple(row.id for row in rows)
            if ids:
                changed = db.execute(update(ServicePaymentAttempt).where(
                    ServicePaymentAttempt.id.in_(ids),
                    ServicePaymentAttempt.visit_id == fresh.visit_id,
                    ServicePaymentAttempt.business_id == fresh.business_id,
                    ServicePaymentAttempt.status == 'pending',
                ).values(status='cancelled', updated_at=fresh.checked_at)
                  .execution_options(synchronize_session=False))
                if changed.rowcount != len(ids):
                    raise PendingPaymentConflict('Pending payment target changed')
            targets.revalidate_action_target(db, raw_session, fresh)
            return ids
    except DBAPIError as exc:
        code = getattr(exc.orig, 'pgcode', None) or getattr(exc.orig, 'sqlstate', None)
        if code in {'55P03', '40P01'}:
            raise targets.ActionBusy('Pending payment is busy; retry after whole rollback') from None
        raise
