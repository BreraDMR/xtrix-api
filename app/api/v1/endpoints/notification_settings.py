"""The owner's switch for client notifications in one business.

The response never includes provider credentials: only whether the configured channel can
actually deliver and which channels this installation offers at all.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core import notification_preferences, notification_scheduler
from app.api.core.i18n import _
from app.api.core.notification_access import notification_business_id
from app.db.models.notification_settings import NotificationSettings
from app.db.models.user import User
from app.schemas.notification_settings import NotificationSettingsOut, NotificationSettingsUpdate

router = APIRouter()


def _out(row: NotificationSettings) -> NotificationSettingsOut:
    return NotificationSettingsOut(
        channel=row.channel,
        reminder_time=row.reminder_time,
        is_enabled=row.is_enabled,
        provider_ready=notification_preferences.channel_ready(row.channel),
        available_channels=notification_preferences.available_channels(),
    )


@router.get("", response_model=NotificationSettingsOut, summary="Get notification settings")
def get_notification_settings(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    business_id = notification_business_id(db, current_user, locale)
    row = notification_preferences.get_or_create_settings(db, business_id)
    return _out(row)


@router.put("", response_model=NotificationSettingsOut, summary="Change notification settings")
def update_notification_settings(
    payload: NotificationSettingsUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    business_id = notification_business_id(db, current_user, locale)
    row = notification_preferences.get_or_create_settings(db, business_id)
    fields = payload.model_dump(exclude_unset=True)

    channel = fields.get("channel", row.channel)
    if "channel" in fields:
        if channel not in notification_preferences.SUPPORTED_CHANNELS:
            raise HTTPException(status_code=409, detail=_("notification_channel_unavailable", locale))
        if not notification_preferences.channel_ready(channel):
            raise HTTPException(status_code=409, detail=_("notification_provider_not_ready", locale))
        row.channel = channel

    if "is_enabled" in fields:
        if fields["is_enabled"]:
            if row.channel not in notification_preferences.SUPPORTED_CHANNELS:
                raise HTTPException(
                    status_code=409, detail=_("notification_channel_unavailable", locale)
                )
            if not notification_preferences.channel_ready(row.channel):
                raise HTTPException(
                    status_code=409, detail=_("notification_provider_not_ready", locale)
                )
        row.is_enabled = fields["is_enabled"]

    if "reminder_time" in fields:
        row.reminder_time = fields["reminder_time"]

    db.commit()
    db.refresh(row)
    notification_scheduler.sync_business_reminders(db, business_id)
    return _out(row)
