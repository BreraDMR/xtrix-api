from typing import Optional
from pydantic import ConfigDict, BaseModel, EmailStr, Field
from app.schemas.limits import BusinessType, Currency, LongText, Phone, ShortText
from app.schemas.datetimes import UtcDatetime
class BusinessBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    business_type: BusinessType
    address: Optional[ShortText] = None
    phone: Optional[Phone] = None
    email: Optional[EmailStr] = None
    description: Optional[LongText] = None
    currency: Currency = Field(default="EUR", description="ISO code: EUR, USD, CZK...")
class BusinessCreate(BusinessBase):
    pass
class BusinessUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    business_type: Optional[BusinessType] = None
    address: Optional[ShortText] = None
    phone: Optional[Phone] = None
    email: Optional[EmailStr] = None
    description: Optional[LongText] = None
    currency: Optional[Currency] = None
class BusinessOut(BusinessBase):
    id: int
    created_at: UtcDatetime
    model_config = ConfigDict(from_attributes=True)
