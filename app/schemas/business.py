from typing import Literal, Optional
from pydantic import ConfigDict, BaseModel, EmailStr, Field, field_validator, model_validator
from app.schemas.limits import BusinessType, Currency, LongText, Phone, ShortText
from app.schemas.datetimes import UtcDatetime
from app.schemas.base import InputModel
from app.schemas.errors import invalid

AssistantGender = Literal["female", "male", "neuter"]


def _assistant_name(v):
    # blank = back to the default name; no control chars, the name ends up in the model prompt
    if v is None:
        return None
    v = v.strip()
    if any(ord(c) < 32 or ord(c) == 127 for c in v):
        raise invalid("invalid_assistant_name", "The assistant name can't contain line breaks or control characters")
    return v or None
def _known_timezone(v):
    if v is None:
        return v
    from zoneinfo import ZoneInfo
    try:
        ZoneInfo(v)
    except Exception:
        raise invalid("invalid_timezone", "Unknown timezone, use an IANA name like Europe/Prague")
    return v


class BusinessBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    business_type: BusinessType
    address: Optional[ShortText] = None
    phone: Optional[Phone] = None
    email: Optional[EmailStr] = None
    description: Optional[LongText] = None
    currency: Currency = Field(default="EUR", description="ISO code: EUR, USD, CZK...")
    timezone: str = Field(default="UTC", max_length=64, description="IANA timezone, e.g. Europe/Prague")
    booking_horizon_days: int = Field(default=30, ge=1, le=365, description="How many days ahead clients can book")
    booking_lead_time_minutes: int = Field(default=0, ge=0, le=10080, description="Minimum notice before a booking, minutes")

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, v):
        return _known_timezone(v)
class BusinessCreate(InputModel, BusinessBase):
    pass
class BusinessUpdate(InputModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    business_type: Optional[BusinessType] = None
    address: Optional[ShortText] = None
    phone: Optional[Phone] = None
    email: Optional[EmailStr] = None
    description: Optional[LongText] = None
    currency: Optional[Currency] = None
    assistant_name: Optional[str] = Field(None, max_length=30, description="Empty = default name for the interface language")
    assistant_gender: Optional[AssistantGender] = Field(None, description="female|male|neuter, for verb forms in ru/cz")
    timezone: Optional[str] = Field(None, max_length=64, description="IANA timezone")
    booking_horizon_days: Optional[int] = Field(None, ge=1, le=365)
    booking_lead_time_minutes: Optional[int] = Field(None, ge=0, le=10080)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, v):
        return _known_timezone(v)

    @model_validator(mode="after")
    def policy_fields_not_null(self):
        for name in ("timezone", "booking_horizon_days", "booking_lead_time_minutes"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise invalid("invalid_choice", f"{name} cannot be null")
        return self
    @field_validator("assistant_name", mode="before")
    @classmethod
    def clean_assistant_name(cls, v):
        return _assistant_name(v) if isinstance(v, str) or v is None else v
    @field_validator("assistant_gender", mode="before")
    @classmethod
    def gender_not_null(cls, v):
        if v is None:
            raise invalid("invalid_choice", "assistant_gender must be one of: female, male, neuter",
                          allowed=["female", "male", "neuter"])
        return v
class BusinessOut(BusinessBase):
    id: int
    currency: str  # whatever is stored, older rows predate the ISO check
    assistant_name: Optional[str] = None
    assistant_gender: str = "female"
    created_at: UtcDatetime
    model_config = ConfigDict(from_attributes=True)
