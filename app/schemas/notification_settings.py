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
    provider_ready: bool
    available_channels: list[str]
    model_config = ConfigDict(from_attributes=True)


class NotificationSettingsUpdate(InputModel):
    channel: Optional[str] = Field(None, max_length=20)
    reminder_time: Optional[str] = None
    is_enabled: Optional[bool] = None

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

    @field_validator("is_enabled")
    @classmethod
    def enabled_not_null(cls, value):
        if value is None:
            raise invalid("invalid_choice", "is_enabled cannot be null")
        return value


class NotificationTestOut(BaseModel):
    status: str
    provider: str
    recipient: str
    subject: str
    preview: str
