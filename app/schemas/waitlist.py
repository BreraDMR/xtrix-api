"""Waitlist contracts.

The guest body carries preferences and consent, never a price, status or tenant id.
The salon list is a separate response: contacts are visible only behind staff auth.
"""
import re
from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.schemas.base import InputModel
from app.schemas.datetimes import UtcDatetime
from app.schemas.errors import invalid
from app.schemas.public_booking import PublicBookingOut

TimePreference = Literal["any", "morning", "afternoon", "evening"]


class WaitlistJoinIn(InputModel):
    model_config = ConfigDict(extra="forbid")

    service_id: int = Field(..., gt=0)
    staff_id: Optional[int] = Field(None, gt=0)
    preferred_date_from: date
    preferred_date_to: Optional[date] = None
    time_preference: TimePreference = "any"
    full_name: str = Field(..., min_length=1, max_length=200)
    email: EmailStr
    phone: Optional[str] = Field(None, max_length=50)
    marketing_consent: bool = False
    processing_consent: bool

    @field_validator("phone", mode="before")
    @classmethod
    def clean_phone(cls, value):
        if value is None or value == "":
            return None
        from app.api.core.security import validate_phone

        cleaned = re.sub(r"[\s\-\(\)\.]", "", str(value))
        is_valid, message = validate_phone(cleaned)
        if not is_valid:
            raise invalid("invalid_phone", message)
        return cleaned

    @field_validator("email")
    @classmethod
    def clean_email(cls, value):
        return str(value).strip().lower()

    @field_validator("marketing_consent", "processing_consent", mode="before")
    @classmethod
    def consent_is_bool(cls, value):
        if not isinstance(value, bool):
            raise invalid("invalid_consent", "Consent must be true or false")
        return value

    @model_validator(mode="after")
    def name_dates_and_consents(self):
        if any(ord(char) < 32 or ord(char) == 127 for char in self.full_name):
            raise invalid("invalid_name", "The name can't contain line breaks or control characters")
        if self.preferred_date_to is not None:
            if self.preferred_date_to < self.preferred_date_from:
                raise invalid("invalid_date_range", "The end date can't be before the start date")
            if (self.preferred_date_to - self.preferred_date_from).days > 31:
                raise invalid("invalid_date_range", "Choose a range of at most 32 days")
        if not self.processing_consent:
            raise invalid(
                "processing_consent_required",
                "Confirm that the waitlist data may be processed",
            )
        return self


class WaitlistJoinOut(BaseModel):
    message: str
    created: bool
    manage_token: Optional[str] = Field(
        None,
        description="Token for leaving the waitlist; returned once, only when the entry is new",
    )


class WaitlistMessageOut(BaseModel):
    message: str


class WaitlistOfferOut(BaseModel):
    business_name: str
    business_phone: Optional[str] = None
    business_address: Optional[str] = None
    service_name: str
    staff_name: str
    start: UtcDatetime
    end: UtcDatetime
    timezone: str
    status: str
    expires_at: UtcDatetime
    price: Optional[float] = None
    currency: str


class WaitlistOfferAcceptOut(PublicBookingOut):
    pass


class WaitlistOfferSummaryOut(BaseModel):
    id: int
    start: UtcDatetime
    end: UtcDatetime
    staff_id: int
    staff_name: str
    status: str
    expires_at: UtcDatetime


class WaitlistEntryOut(BaseModel):
    id: int
    full_name: str
    email: str
    phone: Optional[str] = None
    language: str
    service_id: int
    service_name: str
    specialist_id: Optional[int] = None
    specialist_name: Optional[str] = None
    preferred_date_from: date
    preferred_date_to: Optional[date] = None
    time_preference: str
    status: str
    created_at: UtcDatetime
    latest_offer: Optional[WaitlistOfferSummaryOut] = None
