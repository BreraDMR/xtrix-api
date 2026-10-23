"""Outbox delivery: claim a batch under a lease, send, and record what the provider said.

Rows are claimed with SELECT ... FOR UPDATE SKIP LOCKED and committed as ``processing``
before any network call, so a second worker skips them. A crashed worker leaves the row
processing until the lease expires; another worker then picks it up. The provider message id
is stored before the final status, so a failure between the provider response and bookkeeping
does not send the same message twice.
"""
from datetime import datetime, timedelta
from typing import Callable, Optional

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.api.core import notification_events, notification_preferences
from app.api.core import campaign_service
from app.api.core.client_portal_email import KIND as CLIENT_PORTAL_KIND
from app.api.core.clock import utcnow
from app.api.core.notification_transport import DeliveryResult
from app.db.models.notification_outbox import NotificationOutbox

LEASE = timedelta(minutes=5)
MAX_ATTEMPTS = 5
BATCH_SIZE = 20
BACKOFF_BASE = timedelta(minutes=5)
BACKOFF_CAP = timedelta(hours=1)

SendFunc = Callable[[NotificationOutbox], DeliveryResult]


def _backoff(attempts: int) -> timedelta:
    delay = BACKOFF_BASE * (2 ** max(0, attempts - 1))
    return min(delay, BACKOFF_CAP)


def _error(result: DeliveryResult) -> str:
    text = result.error or result.status
    return text[:500]


def claim_batch(
    db: Session,
    now: Optional[datetime] = None,
    limit: int = BATCH_SIZE,
    max_attempts: int = MAX_ATTEMPTS,
    lease: timedelta = LEASE,
) -> list[int]:
    return list(claim_leases(db, now=now, limit=limit, max_attempts=max_attempts, lease=lease))


def claim_leases(
    db: Session,
    now: Optional[datetime] = None,
    limit: int = BATCH_SIZE,
    max_attempts: int = MAX_ATTEMPTS,
    lease: timedelta = LEASE,
) -> dict[int, int]:
    """Claim rows and return id -> attempts. The attempts value is this worker's lease ticket:
    whoever reclaims the row after an expired lease bumps it, so a stale worker can tell."""
    now = now or utcnow()
    expired = (
        db.query(NotificationOutbox)
        .filter(
            NotificationOutbox.status == "processing",
            NotificationOutbox.locked_until.isnot(None),
            NotificationOutbox.locked_until < now,
        )
        .with_for_update(skip_locked=True)
        .all()
    )
    for row in expired:
        if row.provider_message_id:
            _finish_sent(row, row.provider or "unknown", row.provider_message_id, now)
        elif (row.attempts or 0) >= max_attempts:
            # the worker died on its last allowed attempt; retrying would exceed the limit
            row.status = "failed"
            row.last_error = row.last_error or "lease_expired"
            row.locked_until = None
            row.updated_at = now

    rows = (
        db.query(NotificationOutbox)
        .filter(
            NotificationOutbox.attempts < max_attempts,
            NotificationOutbox.scheduled_at <= now,
            or_(
                NotificationOutbox.status == "pending",
                and_(
                    NotificationOutbox.status == "processing",
                    NotificationOutbox.locked_until.isnot(None),
                    NotificationOutbox.locked_until < now,
                ),
            ),
        )
        .order_by(NotificationOutbox.scheduled_at, NotificationOutbox.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
        .all()
    )
    leases: dict[int, int] = {}
    for row in rows:
        row.status = "processing"
        row.locked_until = now + lease
        row.attempts = (row.attempts or 0) + 1
        row.updated_at = now
        leases[row.id] = row.attempts
    db.commit()
    return leases


def _still_ours(db: Session, message_id: int, ticket: int) -> Optional[NotificationOutbox]:
    row = db.query(NotificationOutbox).filter(NotificationOutbox.id == message_id).populate_existing().first()
    if row is None or row.status != "processing" or row.attempts != ticket:
        return None
    return row


def _finish_sent(row: NotificationOutbox, provider: str, provider_message_id: Optional[str], now: datetime) -> None:
    row.status = "sent"
    row.provider = provider
    row.provider_message_id = provider_message_id
    row.locked_until = None
    row.last_error = None
    row.sent_at = now
    row.updated_at = now


def run_once(
    db: Session,
    send: SendFunc,
    now: Optional[datetime] = None,
    limit: int = BATCH_SIZE,
    max_attempts: int = MAX_ATTEMPTS,
) -> dict[str, int]:
    now = now or utcnow()
    leases = claim_leases(db, now=now, limit=limit, max_attempts=max_attempts)
    stats: dict[str, int] = {
        "claimed": len(leases),
        "sent": 0,
        "captured": 0,
        "retried": 0,
        "failed": 0,
        "skipped": 0,
        "cancelled": 0,
        "lost": 0,
    }
    for message_id, ticket in leases.items():
        row = db.get(NotificationOutbox, message_id)
        if row is None:
            continue
        # see a cancellation that happened after the claim committed the lease
        db.refresh(row)
        if row.status == "cancelled":
            stats["cancelled"] += 1
            continue
        if row.status != "processing" or row.attempts != ticket:
            # our lease ran out and another worker owns (or already finished) this row
            stats["lost"] += 1
            continue
        if row.provider_message_id:
            # the provider already accepted this message before the previous worker stopped
            _finish_sent(row, row.provider or "unknown", row.provider_message_id, now)
            db.commit()
            stats["sent"] += 1
            continue
        if row.kind == campaign_service.KIND:
            blocked = campaign_service.campaign_delivery_block(db, row)
            if blocked is not None:
                # withdrawn consent or an owner's cancel must win over a queued message
                row.status = "cancelled"
                row.locked_until = None
                row.last_error = blocked
                row.updated_at = now
                db.commit()
                stats["cancelled"] += 1
                continue
        if row.kind == notification_events.KIND_REMINDER and not notification_events.reminder_still_due(db, row):
            # cancel/reschedule only drop pending rows, so a row we already hold is checked here
            row.status = "cancelled"
            row.locked_until = None
            row.last_error = "visit_changed"
            row.updated_at = now
            db.commit()
            stats["cancelled"] += 1
            continue
        # account-level messages (password reset) and client sign-in links are transactional:
        # a business reminder switch must not block access to the visitor's own cabinet
        channel = (
            row.channel
            if row.business_id is None or row.kind == CLIENT_PORTAL_KIND
            else notification_preferences.enabled_channel(db, row.business_id)
        )
        if channel is None or row.channel != channel:
            # the owner turned notifications off after this message was queued
            row.status = "cancelled"
            row.locked_until = None
            row.last_error = "notifications_disabled"
            row.updated_at = now
            db.commit()
            stats["cancelled"] += 1
            continue
        if _still_ours(db, message_id, ticket) is None:
            stats["lost"] += 1
            continue
        try:
            result = send(row)
        except Exception as exc:
            result = DeliveryResult("failed", "worker", error=f"send_error:{type(exc).__name__}")
        if _still_ours(db, message_id, ticket) is None:
            # a slow send outlived the lease; the row's new owner writes the outcome
            stats["lost"] += 1
            continue
        if result.status == "sent":
            row.provider = result.provider
            row.provider_message_id = result.provider_message_id
            row.updated_at = now
            # make the provider id durable before the final status: a later crash must not resend
            db.commit()
            row = db.get(NotificationOutbox, message_id)
            if row is None:
                continue
            _finish_sent(row, result.provider, result.provider_message_id, now)
            db.commit()
            stats["sent"] += 1
        elif result.status == "captured":
            row.status = "captured"
            row.provider = result.provider
            row.locked_until = None
            row.updated_at = now
            db.commit()
            stats["captured"] += 1
        elif result.status == "skipped":
            row.status = "skipped"
            row.provider = result.provider
            row.locked_until = None
            row.last_error = _error(result)
            row.updated_at = now
            db.commit()
            stats["skipped"] += 1
        else:
            row.provider = result.provider
            row.last_error = _error(result)
            row.locked_until = None
            row.updated_at = now
            if (row.attempts or 0) >= max_attempts:
                row.status = "failed"
                stats["failed"] += 1
            else:
                row.status = "pending"
                row.scheduled_at = now + _backoff(row.attempts or 1)
                stats["retried"] += 1
            db.commit()
    return stats


def requeue_failed(db: Session, message_id: int, business_id: int) -> bool:
    row = (
        db.query(NotificationOutbox)
        .filter(
            NotificationOutbox.id == message_id,
            NotificationOutbox.business_id == business_id,
            NotificationOutbox.status.in_(("failed", "skipped")),
        )
        .first()
    )
    if not row or row.provider_message_id:
        return False
    row.status = "pending"
    row.attempts = 0
    row.locked_until = None
    row.scheduled_at = utcnow()
    row.last_error = None
    row.updated_at = utcnow()
    db.commit()
    return True
