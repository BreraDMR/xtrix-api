"""Visit reminders on the visitor's phone through Expo Push.

Each device gets its own outbox row (``<reminder key>:push:<device id>``), so one dead token
never holds back the others. Who may get a reminder is decided twice: when the row is queued
and again right before sending, because a logout, an unregistered phone or a revoked grant can
land in between. The push text carries no manage link - a phone's notification history is not
a safe place for a live booking token - only the visit id, so the app opens its own screen.
"""
import re
from typing import Optional

from sqlalchemy import and_, case, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.api.core import notification_events
from app.api.core.clock import utcnow
from app.api.core.config import settings
from app.api.core.notification_outbox import enqueue_notification
from app.api.core.notification_transport import OutboundMessage
from app.db.models.business import Business
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.visit import Visit
from app.db.models.visitor_access import VisitorGrant
from app.db.models.visitor_account import VisitorAccount
from app.db.models.visitor_auth import VisitorSession
from app.db.models.visitor_push_token import VisitorPushToken

CHANNEL = "push"
PLATFORMS = ("ios", "android")
TOKEN_PATTERN = re.compile(r"^(ExponentPushToken|ExpoPushToken)\[[A-Za-z0-9_-]{1,200}\]$")
FALLBACK = "en"
# what a queued or sent phone reminder keeps once its device is forgotten
ERASED_RECIPIENT = "deleted"

_TEXTS = {
    "en": ("Appointment reminder", "{service} at {team}, {when}"),
    "ru": ("Напоминание о визите", "{service} в {team}, {when}"),
    "cz": ("Připomínka návštěvy", "{service} v {team}, {when}"),
}


def device_key(reminder_key: str, device_id: int) -> str:
    return f"{reminder_key}{notification_events.PUSH_KEY_MARK}{device_id}"


def _device_id(row: NotificationOutbox) -> Optional[int]:
    _, mark, tail = row.event_key.partition(notification_events.PUSH_KEY_MARK)
    return int(tail) if mark and tail.isdigit() else None


def register(db: Session, session: VisitorSession, token: str, platform: str, language: Optional[str]) -> None:
    """Attach the phone to this session; a phone has one owner, the last one who signed in on it."""
    now = utcnow()
    values = {"session_id": session.id, "token": token, "platform": platform,
              "language": language, "updated_at": now}
    db.execute(pg_insert(VisitorPushToken).values(created_at=now, **values)
               .on_conflict_do_update(constraint="uq_visitor_push_tokens_token", set_=values))


def unregister(db: Session, session: VisitorSession, token: str) -> None:
    """Forget the phone if it belongs to this account; someone else's token is left alone."""
    own_sessions = db.query(VisitorSession.id).filter(VisitorSession.account_id == session.account_id)
    removed = (db.query(VisitorPushToken)
               .filter(VisitorPushToken.token == token, VisitorPushToken.session_id.in_(own_sessions))
               .delete(synchronize_session=False))
    if removed:
        _erase(db, [token])


def forget(db: Session, token: str) -> None:
    """Expo said the app is gone from the phone; keep no address that can never be reached."""
    db.query(VisitorPushToken).filter(VisitorPushToken.token == token).delete(synchronize_session=False)
    _erase(db, [token])


def forget_session_devices(db: Session, *session_criteria) -> None:
    """A signed-out session takes its phones with it, so a shared phone stops showing your visits."""
    tokens = [token for (token,) in db.query(VisitorPushToken.token)
              .join(VisitorSession, VisitorSession.id == VisitorPushToken.session_id)
              .filter(*session_criteria)]
    if not tokens:
        return
    db.query(VisitorPushToken).filter(VisitorPushToken.token.in_(tokens)).delete(synchronize_session=False)
    _erase(db, tokens)


def erase_account_devices(db: Session, account_id: int) -> None:
    """Account deletion: the session cascade drops the devices, this clears their trace in the queue."""
    tokens = [token for (token,) in db.query(VisitorPushToken.token)
              .join(VisitorSession, VisitorSession.id == VisitorPushToken.session_id)
              .filter(VisitorSession.account_id == account_id)]
    _erase(db, tokens)


def _erase(db: Session, tokens: list[str]) -> None:
    if not tokens:
        return
    (db.query(NotificationOutbox)
     .filter(NotificationOutbox.channel == CHANNEL, NotificationOutbox.recipient.in_(tokens))
     .update({NotificationOutbox.status: case((NotificationOutbox.status == "pending", "cancelled"),
                                               else_=NotificationOutbox.status),
              NotificationOutbox.recipient: ERASED_RECIPIENT,
              NotificationOutbox.updated_at: utcnow()},
             synchronize_session=False))


def _cancel_pending(db: Session, *criteria) -> int:
    return (db.query(NotificationOutbox)
            .filter(NotificationOutbox.channel == CHANNEL, NotificationOutbox.status == "pending", *criteria)
            .update({NotificationOutbox.status: "cancelled", NotificationOutbox.updated_at: utcnow()},
                    synchronize_session=False))


def cancel_pending_for_visit(db: Session, visit_id: int) -> int:
    return _cancel_pending(db, NotificationOutbox.visit_id == visit_id,
                           NotificationOutbox.kind == notification_events.KIND_REMINDER)


def live_devices(db: Session, visit: Visit) -> list[VisitorPushToken]:
    """Phones with a live session whose account may see this visit right now."""
    if (visit.business_id is None or visit.deleted_at is not None
            or (visit.business and visit.business.deleted_at is not None)
            or (visit.client and visit.client.deleted_at is not None)):
        return []
    now = utcnow()
    granted = (db.query(VisitorGrant.id)
               .filter(VisitorGrant.account_id == VisitorSession.account_id,
                       VisitorGrant.revoked_at.is_(None),
                       VisitorGrant.business_id == visit.business_id,
                       VisitorGrant.client_id == visit.client_id,
                       or_(and_(VisitorGrant.scope == "visit", VisitorGrant.visit_id == visit.id),
                           and_(VisitorGrant.scope == "client", VisitorGrant.visit_id.is_(None))))
               .exists())
    return (db.query(VisitorPushToken)
            .join(VisitorSession, VisitorSession.id == VisitorPushToken.session_id)
            .join(VisitorAccount, VisitorAccount.id == VisitorSession.account_id)
            .filter(VisitorSession.revoked_at.is_(None), VisitorSession.expires_at > now,
                    VisitorAccount.email_verified_at.is_not(None), granted)
            .order_by(VisitorPushToken.id)
            .all())


def ensure_reminders(db: Session, business: Business, visit: Visit) -> int:
    """Queue or revive the phone reminder of every device that can see the visit."""
    from app.api.core import notification_preferences

    when = notification_events.reminder_at(visit, business, notification_preferences.reminder_time(db, business.id))
    if (not settings.push_notifications_enabled or visit.status != "planned"
            or when is None or when <= utcnow()):
        cancel_pending_for_visit(db, visit.id)
        return 0
    reminder_key = notification_events.reminder_event_key(visit)
    queued = 0
    for device in live_devices(db, visit):
        key = device_key(reminder_key, device.id)
        existing = db.query(NotificationOutbox).filter(NotificationOutbox.event_key == key).first()
        if existing is not None:
            if existing.status == "cancelled":
                existing.status = "pending"
                existing.scheduled_at = when
                existing.attempts = 0
                existing.last_error = None
                existing.updated_at = utcnow()
                queued += 1
            continue
        enqueue_notification(
            db,
            business_id=business.id,
            visit_id=visit.id,
            event_key=key,
            kind=notification_events.KIND_REMINDER,
            channel=CHANNEL,
            recipient=device.token,
            language=device.language,
            payload={
                "business_name": business.name,
                "business_timezone": business.timezone,
                "service_name": visit.service_name or "",
                "start": visit.date_start.isoformat() if visit.date_start else None,
                "visit_id": visit.id,
            },
            scheduled_at=when,
        )
        queued += 1
    return queued


def still_deliverable(db: Session, row: NotificationOutbox) -> bool:
    if not settings.push_notifications_enabled:
        return False
    visit = db.query(Visit).filter(Visit.id == row.visit_id).populate_existing().first() if row.visit_id else None
    if visit is None:
        return False
    device_id = _device_id(row)
    return any(device.id == device_id and device.token == row.recipient for device in live_devices(db, visit))


def render(row: NotificationOutbox) -> OutboundMessage:
    from app.api.core.notification_templates import _when

    payload = row.payload or {}
    title, body = _TEXTS[row.language if row.language in _TEXTS else FALLBACK]
    body = body.format(service=payload.get("service_name") or "", team=payload.get("business_name") or "Xtrix",
                       when=_when(payload.get("start"), payload.get("business_timezone")))
    return OutboundMessage(channel=CHANNEL, recipient=row.recipient, subject=title, text=body.lstrip(),
                           data={"visit_id": payload.get("visit_id") or row.visit_id})
