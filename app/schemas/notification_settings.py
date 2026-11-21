"""Notification settings contracts for the business owner."""
import re
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.base import InputModel
from app.schemas.errors import invalid

_TIME = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")


class NotificationSettingsOut(BaseModel):
    channel: str
    reminder_time: str
    is_enabled: bool
    birthday_greetings_enabled: bool = False
    visit_followups_enabled: bool = False
    crm_visit_messages_enabled: bool = True
    provider_ready: bool
    available_channels: list[str]
    model_config = ConfigDict(from_attributes=True)


class NotificationSettingsUpdate(InputModel):
    channel: Optional[str] = Field(None, max_length=20)
    reminder_time: Optional[str] = None
    is_enabled: Optional[bool] = None
    # marketing: only clients with an explicit opt-in get these, and only while this is on
    birthday_greetings_enabled: Optional[bool] = None
    visit_followups_enabled: Optional[bool] = None
    # transactional: created/moved/cancelled in the CRM -> the client hears about it
    crm_visit_messages_enabled: Optional[bool] = None

    @field_validator("channel")
    @classmethod
    def normalize_channel(cls, value):
        if value is None:
            return value
        value = value.strip().lower()
        if not value:
            raise invalid("invalid_choice", "Channel cannot be empty")
        return value

    @field_validator("reminder_time")
    @classmethod
    def valid_reminder_time(cls, value):
        if value is None:
            raise invalid("invalid_time", "Use HH:MM in the business timezone")
        if not _TIME.fullmatch(value):
            raise invalid("invalid_time", "Use HH:MM in the business timezone")
        return value

    @field_validator(
        "is_enabled", "birthday_greetings_enabled", "visit_followups_enabled", "crm_visit_messages_enabled"
    )
    @classmethod
    def enabled_not_null(cls, value, info):
        if value is None:
            raise invalid("invalid_choice", f"{info.field_name} cannot be null")
        return value


class NotificationTestOut(BaseModel):
    status: str
    provider: str
    recipient: str
    subject: str
    preview: str
