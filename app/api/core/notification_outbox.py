"""Producer side of the notification outbox.

The caller owns the transaction: a queued message commits or rolls back together with the
visit change that caused it. The unique event key makes a retried web request a no-op.
"""
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.api.core.clock import utcnow
from app.db.models.notification_outbox import NotificationOutbox


def enqueue_notification(
    db: Session,
    *,
    event_key: str,
    kind: str,
    recipient: str,
    business_id: Optional[int] = None,
    user_id: Optional[int] = None,
    channel: str = "email",
    language: Optional[str] = None,
    payload: Optional[dict[str, Any]] = None,
    visit_id: Optional[int] = None,
    campaign_id: Optional[int] = None,
    scheduled_at: Optional[datetime] = None,
) -> NotificationOutbox:
    values = {
        "business_id": business_id,
        "user_id": user_id,
        "visit_id": visit_id,
        "campaign_id": campaign_id,
        "event_key": event_key,
        "kind": kind,
        "channel": channel,
        "recipient": recipient,
        "language": language,
        "payload": payload or {},
        "scheduled_at": scheduled_at or utcnow(),
    }
    stmt = (
        pg_insert(NotificationOutbox)
        .values(**values)
        .on_conflict_do_nothing(index_elements=[NotificationOutbox.event_key])
    )
    db.execute(stmt)
    return db.query(NotificationOutbox).filter(NotificationOutbox.event_key == event_key).one()
