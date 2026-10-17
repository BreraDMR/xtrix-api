from datetime import timedelta
from sqlalchemy.orm import Session
from app.db.models.subscription import Subscription
from app.db.models.user import User
from app.api.core import notify
from app.api.core.logger import logger, mask_email
from app.api.core.clock import utcnow
from app.api.core.sent_log import mark_sent, was_sent
from app.api.core.staff_messages import contact_for, subscription_expiry


def send_subscription_expiry_notifications(db: Session, days_before: int = 3) -> int:
    """Warn owners whose paid plan ends in `days_before` days, in their own UI language."""
    start = utcnow() + timedelta(days=days_before)
    expiring = (
        db.query(Subscription)
        .join(User, Subscription.user_id == User.id)
        .filter(
            Subscription.status.in_(["active", "pending_cancellation"]),
            Subscription.current_period_end >= start,
            Subscription.current_period_end < start + timedelta(days=1),
            Subscription.is_trial == False,  # noqa: E712
            User.deleted_at.is_(None),
        )
        .all()
    )
    sent_count = 0
    for sub in expiring:
        key = f"{sub.id}:{days_before}:{sub.current_period_end.date()}"
        if was_sent(db, "subscription_expiry", key):
            continue
        try:
            contact = contact_for(db, sub.user)
            msg = subscription_expiry(contact.language, sub.user.full_name, sub.current_period_end, days_before)
            sent = False
            if contact.email:
                sent = notify._send_email(contact.email, msg.subject, msg.body) or sent
            if contact.phone:
                sent = notify._send_sms(contact.phone, msg.short) or sent
            # count what actually went out, not just who we tried
            if sent:
                mark_sent(db, "subscription_expiry", key)
                sent_count += 1
                logger.info(f"Sent expiry notice for subscription {sub.id} to {mask_email(contact.email)}")
        except Exception as e:
            logger.error(f"Failed to send notification for subscription {sub.id}: {e}")
    return sent_count


def check_and_notify_expiring_subscriptions(db: Session) -> int:
    return sum(send_subscription_expiry_notifications(db, days_before=d) for d in (7, 3, 1, 0))
