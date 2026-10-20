"""Turn planned visits into idempotent reminder rows.

A reminder is due at the same local time one day before the visit in the business timezone,
so a DST jump does not move the hour. The sweep also cancels pending reminders whose visit
was moved, cancelled, completed or deleted while no job was running.
"""
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session, joinedload

from app.api.core import notification_events
from app.api.core.clock import utcnow
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.visit import Visit


def sync_reminders(
    db: Session, now: Optional[datetime] = None, horizon_days: int = 7
) -> dict[str, int]:
    now = now or utcnow()
    until = now + timedelta(days=horizon_days)
    visits = (
        db.query(Visit)
        .options(joinedload(Visit.business), joinedload(Visit.client))
        .filter(
            Visit.status == "planned",
            Visit.deleted_at.is_(None),
            Visit.archived_at.is_(None),
            Visit.date_start > now,
            Visit.date_start <= until,
        )
        .all()
    )

    scheduled = 0
    cancelled = 0
    seen: set[int] = set()
    for visit in visits:
        if not visit.business:
            continue
        key = notification_events.reminder_event_key(visit)
        cancelled += notification_events.cancel_pending_reminders(db, visit.id, keep_key=key)
        if notification_events.ensure_reminder(db, visit.business, visit, visit.client):
            scheduled += 1
        seen.add(visit.id)

    pending = (
        db.query(NotificationOutbox)
        .filter(
            NotificationOutbox.kind == notification_events.KIND_REMINDER,
            NotificationOutbox.status == "pending",
        )
        .all()
    )
    pending_visit_ids = {row.visit_id for row in pending if row.visit_id is not None}
    visits_by_id = (
        {visit.id: visit for visit in db.query(Visit).filter(Visit.id.in_(pending_visit_ids)).all()}
        if pending_visit_ids
        else {}
    )
    for row in pending:
        if row.visit_id in seen:
            continue
        visit = visits_by_id.get(row.visit_id)
        if (
            visit is None
            or visit.status != "planned"
            or visit.deleted_at is not None
            or visit.archived_at is not None
            or notification_events.reminder_event_key(visit) != row.event_key
        ):
            row.status = "cancelled"
            row.updated_at = now
            cancelled += 1

    db.commit()
    return {"scheduled": scheduled, "cancelled": cancelled}
