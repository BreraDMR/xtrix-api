from datetime import date, datetime
from typing import Optional
import re

from pydantic import BaseModel, Field, EmailStr, field_validator


class ClientBase(BaseModel):
    full_name: str = Field(..., max_length=255, min_length=1)
    birth_date: Optional[date] = None
    gender: Optional[str] = Field(None, description="male|female|other")
    allergies: Optional[str] = None
    chronic_diseases: Optional[str] = None
    pregnancy: Optional[bool] = False
    ontology: Optional[dict] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[EmailStr] = None
    viral_diseases: Optional[str] = None
    inventory: Optional[dict] = None
    spending: Optional[float] = Field(0.0, ge=0.0)
    birthday_discount: Optional[bool] = False
    personal_notes: Optional[str] = None
    location_lat: Optional[float] = Field(None, ge=-90, le=90)
    location_lon: Optional[float] = Field(None, ge=-180, le=180)
    
    @field_validator('gender')
    @classmethod
    def validate_gender(cls, v):
        if v is not None and v not in ['male', 'female', 'other']:
            raise ValueError("Gender must be one of: male, female, other")
        return v
    
    @field_validator('contact_phone')
    @classmethod
    def validate_phone(cls, v):
        if v is not None and v != "":
            from app.api.core.security import validate_phone
            is_valid, error_msg = validate_phone(v)
            if not is_valid:
                raise ValueError(error_msg)
        return v
    
    @field_validator('contact_email')
    @classmethod
    def validate_email(cls, v):
        if v is not None and v != "":
            from app.api.core.security import validate_email
            is_valid, error_msg = validate_email(v)
            if not is_valid:
                raise ValueError(error_msg)
        return v
    
    @field_validator('birth_date')
    @classmethod
    def validate_birth_date(cls, v):
        if v is not None:
            from app.api.core.date_validation import validate_birth_date
            is_valid, error_msg = validate_birth_date(v)
            if not is_valid:
                raise ValueError(error_msg)
        return v


class ClientCreate(ClientBase):
    owner_id: int


class ClientUpdate(BaseModel):
    full_name: Optional[str] = None
    birth_date: Optional[date] = None
    gender: Optional[str] = None
    allergies: Optional[str] = None
    chronic_diseases: Optional[str] = None
    pregnancy: Optional[bool] = None
    ontology: Optional[dict] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    viral_diseases: Optional[str] = None
    inventory: Optional[dict] = None
    spending: Optional[float] = None
    birthday_discount: Optional[bool] = None
    personal_notes: Optional[str] = None
    location_lat: Optional[float] = None
    location_lon: Optional[float] = None


class ClientOut(ClientBase):
    id: int
    owner_id: int
    created_at: datetime

    class Config:
        from_attributes = True



