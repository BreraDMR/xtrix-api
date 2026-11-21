"""Per-business notification settings and what this installation can actually deliver.

A channel is ready only when its live provider is configured. Enabling a channel that cannot
deliver would show the owner a switch that silently does nothing, so readiness is checked
both when the setting is changed and when a message is queued.
"""
from typing import Optional

from sqlalchemy.orm import Session

from app.api.core.config import settings
from app.db.models.notification_settings import NotificationSettings

SUPPORTED_CHANNELS = ("email",)
DEFAULT_REMINDER_TIME = "10:00"
# per-salon on/off for single message types, on top of the main is_enabled switch
MESSAGE_SWITCHES = ("birthday_greetings_enabled", "visit_followups_enabled", "crm_visit_messages_enabled")


def channel_ready(channel: str) -> bool:
    if channel == "email":
        # the pilot gate counts too: without it the transport drops every email, so
        # showing "ready" would promise reminders that never leave
        return settings.email_delivery_available
    # SMS and WhatsApp stay off until a separate product decision activates them
    return False


def available_channels() -> list[str]:
    return [channel for channel in SUPPORTED_CHANNELS if channel_ready(channel)]


def get_settings(db: Session, business_id: int) -> Optional[NotificationSettings]:
    return (
        db.query(NotificationSettings)
        .filter(NotificationSettings.business_id == business_id)
        .first()
    )


def get_or_create_settings(db: Session, business_id: int) -> NotificationSettings:
    row = get_settings(db, business_id)
    if row is None:
        row = NotificationSettings(business_id=business_id)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def enabled_channel(db: Session, business_id: int) -> Optional[str]:
    row = get_settings(db, business_id)
    if row is None or not row.is_enabled:
        return None
    return row.channel if channel_ready(row.channel) else None


def reminder_time(db: Session, business_id: int) -> str:
    row = get_settings(db, business_id)
    return row.reminder_time if row and row.reminder_time else DEFAULT_REMINDER_TIME


def message_channel(db: Session, business_id: Optional[int], switch: str) -> Optional[str]:
    """The channel for one message type, or None when the salon (or the provider) says no.

    Needs the main switch, the provider and the type's own switch all on. A visit without
    a business has nobody to ask, so it gets nothing.
    """
    if business_id is None:
        return None
    row = get_settings(db, business_id)
    if row is None:
        # no row yet means defaults: the main switch is off, so nothing goes out
        return None
    if not row.is_enabled or not getattr(row, switch):
        return None
    return row.channel if channel_ready(row.channel) else None
