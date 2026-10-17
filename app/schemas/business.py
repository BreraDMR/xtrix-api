from typing import Optional
from datetime import datetime
from pydantic import ConfigDict, BaseModel, EmailStr, Field
from app.schemas.limits import BusinessType, Currency, LongText, Phone, ShortText
class BusinessBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    # plain str here: responses carry the localized label ("Beauty Salon"), see localize_response
    business_type: str
    address: Optional[ShortText] = None
    phone: Optional[Phone] = None
    email: Optional[EmailStr] = None
    description: Optional[LongText] = None
    currency: Currency = Field(default="EUR", description="ISO code: EUR, USD, CZK...")
class BusinessCreate(BusinessBase):
    business_type: BusinessType
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
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
