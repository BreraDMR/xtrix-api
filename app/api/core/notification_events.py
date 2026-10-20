"""Queue booking lifecycle messages inside the visit transaction.

enqueue_notification() only adds a row to the session: the caller commits the visit change
and the notification together, so a rollback never leaves a message about a booking that
does not exist.
"""
from typing import Optional

from sqlalchemy.orm import Session

from app.api.core.notification_outbox import enqueue_notification
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.visit import Visit

KIND_CREATED = "booking_created"
KIND_CHANGED = "booking_changed"
KIND_CANCELLED = "booking_cancelled"


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
    )


def queue_booking_created(
    db: Session, business: Business, visit: Visit, client: Optional[Client] = None
) -> None:
    _queue(db, business, visit, client or visit.client, KIND_CREATED, f"visit:{visit.id}:created")


def queue_booking_changed(
    db: Session, business: Business, visit: Visit, client: Optional[Client] = None
) -> None:
    key = f"visit:{visit.id}:changed:{visit.date_start.isoformat()}"
    _queue(db, business, visit, client or visit.client, KIND_CHANGED, key)


def queue_booking_cancelled(
    db: Session, business: Business, visit: Visit, client: Optional[Client] = None
) -> None:
    _queue(db, business, visit, client or visit.client, KIND_CANCELLED, f"visit:{visit.id}:cancelled")
