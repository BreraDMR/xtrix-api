"""Queue booking lifecycle messages inside the visit transaction.

enqueue_notification() only adds a row to the session: the caller commits the visit change
and the notification together, so a rollback never leaves a message about a booking that
does not exist.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.api.core.availability import salon_zone
from app.api.core.clock import utcnow
from app.api.core.notification_outbox import enqueue_notification
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.visit import Visit

KIND_CREATED = "booking_created"
KIND_CHANGED = "booking_changed"
KIND_CANCELLED = "booking_cancelled"
KIND_REMINDER = "booking_reminder"


def reminder_event_key(visit: Visit) -> str:
    return f"visit:{visit.id}:reminder:{visit.date_start.isoformat()}"


def reminder_at(visit: Visit, business: Business) -> Optional[datetime]:
    """Same local time one day before the visit, so a DST change does not move the hour."""
    if not visit.date_start:
        return None
    zone = salon_zone(business.timezone)
    local_start = visit.date_start.replace(tzinfo=timezone.utc).astimezone(zone)
    local_reminder = local_start - timedelta(days=1)
    return local_reminder.astimezone(timezone.utc).replace(tzinfo=None)


def cancel_pending_reminders(db: Session, visit_id: int, keep_key: Optional[str] = None) -> int:
    query = db.query(NotificationOutbox).filter(
        NotificationOutbox.visit_id == visit_id,
        NotificationOutbox.kind == KIND_REMINDER,
        NotificationOutbox.status == "pending",
    )
    if keep_key:
        query = query.filter(NotificationOutbox.event_key != keep_key)
    return query.update(
        {NotificationOutbox.status: "cancelled", NotificationOutbox.updated_at: utcnow()},
        synchronize_session=False,
    )



def _contact_email(client: Optional[Client]) -> Optional[str]:
    email = (client.contact_email or "").strip() if client else ""
    return email or None


def _payload(business: Business, visit: Visit, client: Client) -> dict:
    return {
        "business_name": business.name,
        "business_timezone": business.timezone,
        "client_name": client.full_name,
        "service_name": visit.service_name or "",
        "start": visit.date_start.isoformat() if visit.date_start else None,
        "visit_id": visit.id,
    }


def _queue(
    db: Session,
    business: Business,
    visit: Visit,
    client: Optional[Client],
    kind: str,
    event_key: str,
    scheduled_at: Optional[datetime] = None,
) -> None:
    email = _contact_email(client)
    if not email:
        return
    enqueue_notification(
        db,
        business_id=business.id,
        visit_id=visit.id,
        event_key=event_key,
        kind=kind,
        recipient=email,
        language=client.language,
        payload=_payload(business, visit, client),
        scheduled_at=scheduled_at,
    )


def ensure_reminder(
    db: Session, business: Business, visit: Visit, client: Optional[Client] = None
) -> bool:
    """Create or revive the reminder for the visit's current start; returns True if queued."""
    client = client or visit.client
    when = reminder_at(visit, business)
    if visit.status != "planned" or not _contact_email(client) or when is None or when <= utcnow():
        cancel_pending_reminders(db, visit.id)
        return False
    key = reminder_event_key(visit)
    existing = db.query(NotificationOutbox).filter(NotificationOutbox.event_key == key).first()
    if existing:
        # a visit moved back to an earlier time gets its cancelled reminder back
        if existing.status == "cancelled":
            existing.status = "pending"
            existing.scheduled_at = when
            existing.attempts = 0
            existing.last_error = None
            existing.updated_at = utcnow()
            return True
        return False
    _queue(db, business, visit, client, KIND_REMINDER, key, scheduled_at=when)
    return True


def queue_booking_created(
    db: Session, business: Business, visit: Visit, client: Optional[Client] = None
) -> None:
    client = client or visit.client
    _queue(db, business, visit, client, KIND_CREATED, f"visit:{visit.id}:created")
    ensure_reminder(db, business, visit, client)


def queue_booking_changed(
    db: Session, business: Business, visit: Visit, client: Optional[Client] = None
) -> None:
    client = client or visit.client
    cancel_pending_reminders(db, visit.id)
    # the timestamp keeps two separate moves to the same time from sharing one message
    key = f"visit:{visit.id}:changed:{visit.date_start.isoformat()}:{utcnow().isoformat()}"
    _queue(db, business, visit, client, KIND_CHANGED, key)
    ensure_reminder(db, business, visit, client)


def queue_booking_cancelled(
    db: Session, business: Business, visit: Visit, client: Optional[Client] = None
) -> None:
    client = client or visit.client
    cancel_pending_reminders(db, visit.id)
    _queue(db, business, visit, client, KIND_CANCELLED, f"visit:{visit.id}:cancelled")
