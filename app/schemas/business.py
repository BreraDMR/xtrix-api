from typing import Literal, Optional
from pydantic import ConfigDict, BaseModel, EmailStr, Field, computed_field, field_validator, model_validator
from app.schemas.limits import BusinessType, Currency, LongText, Phone, ShortText
from app.schemas.datetimes import UtcDatetime
from app.schemas.base import InputModel
from app.schemas.errors import invalid
from app.db.models.specialist_type import SpecialistType, category_value

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


def _rule_tag(v):
    # the tag lands in the client's tag list, so it follows the same rules as tags typed by staff
    if v is None:
        return v
    from app.schemas.client_profile import plain_client_text
    v = " ".join(str(v).split())
    if not v:
        raise invalid("empty_tag", "Tags cannot be empty")
    return plain_client_text(v, "tag")


NOSHOW_RULE_FIELDS = ("late_cancel_window_hours", "noshow_rule_enabled", "noshow_rule_threshold",
                      "noshow_rule_period_months", "noshow_rule_tag", "noshow_rule_block_online")


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
    late_cancel_window_hours: int = Field(default=24, ge=0, le=336,
        description="A cancel closer than this to the start is a late cancel; 0 = don't track")
    noshow_rule_enabled: bool = Field(default=False, description="Apply the 'N no-shows within M months' rule")
    noshow_rule_threshold: int = Field(default=3, ge=1, le=50, description="N: no-shows that trigger the rule")
    noshow_rule_period_months: int = Field(default=6, ge=1, le=36, description="M: months to look back")
    noshow_rule_tag: str = Field(default="no-show", min_length=1, max_length=40, description="Tag added to the client")
    noshow_rule_block_online: bool = Field(default=False, description="Also stop the client's online booking")

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, v):
        return _known_timezone(v)

    @field_validator("noshow_rule_tag")
    @classmethod
    def valid_rule_tag(cls, v):
        return _rule_tag(v)
SPECIALTY_DESCRIPTION = "Main specialty, one of GET /specialist-profiles/types; the front can shape the UI by its category"


class BusinessCreate(InputModel, BusinessBase):
    # left out = the owner's own specialty from signup, if they picked one
    specialist_type: Optional[SpecialistType] = Field(None, description=SPECIALTY_DESCRIPTION)
    model_config = ConfigDict(use_enum_values=True)
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
    late_cancel_window_hours: Optional[int] = Field(None, ge=0, le=336)
    noshow_rule_enabled: Optional[bool] = None
    noshow_rule_threshold: Optional[int] = Field(None, ge=1, le=50)
    noshow_rule_period_months: Optional[int] = Field(None, ge=1, le=36)
    noshow_rule_tag: Optional[str] = Field(None, min_length=1, max_length=40)
    noshow_rule_block_online: Optional[bool] = None
    specialist_type: Optional[SpecialistType] = Field(None, description=SPECIALTY_DESCRIPTION + "; null clears it")
    model_config = ConfigDict(use_enum_values=True)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, v):
        return _known_timezone(v)

    @field_validator("noshow_rule_tag")
    @classmethod
    def valid_rule_tag(cls, v):
        return _rule_tag(v)

    @model_validator(mode="after")
    def policy_fields_not_null(self):
        for name in ("timezone", "booking_horizon_days", "booking_lead_time_minutes", *NOSHOW_RULE_FIELDS):
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
    specialist_type: Optional[str] = None
    created_at: UtcDatetime
    model_config = ConfigDict(from_attributes=True)

    @computed_field(description="beauty|wellness|healthcare, from specialist_type; null when not picked")
    @property
    def specialist_category(self) -> Optional[str]:
        return category_value(self.specialist_type)
