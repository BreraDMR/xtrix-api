from typing import Literal, Optional
from pydantic import ConfigDict, BaseModel, EmailStr, Field, field_validator
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
class BusinessBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    business_type: BusinessType
    address: Optional[ShortText] = None
    phone: Optional[Phone] = None
    email: Optional[EmailStr] = None
    description: Optional[LongText] = None
    currency: Currency = Field(default="EUR", description="ISO code: EUR, USD, CZK...")
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
