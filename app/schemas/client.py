from datetime import date, datetime
from typing import Optional
import re

from pydantic import BaseModel, Field, EmailStr, field_validator


class ClientBase(BaseModel):
    """
    Базовая схема для клиента.
    Содержит только глобальные данные клиента, не привязанные к бизнесу.
    Медицинские данные находятся в MedicalProfile.
    Бизнес-специфичные данные находятся в ClientBusinessRelation.
    """
    full_name: str = Field(..., max_length=255, min_length=1)
    birth_date: Optional[date] = None
    gender: Optional[str] = Field(None, description="male|female|other")
    contact_phone: Optional[str] = None
    contact_email: Optional[EmailStr] = None
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
    owner_id: Optional[int] = None  # Made optional for global clients


class ClientUpdate(BaseModel):
    """
    Схема для обновления клиента.
    Содержит только глобальные данные клиента.
    Медицинские данные обновляются через MedicalProfile.
    Бизнес-специфичные данные обновляются через ClientBusinessRelation.
    """
    full_name: Optional[str] = Field(None, max_length=255, min_length=1)
    birth_date: Optional[date] = None
    gender: Optional[str] = Field(None, description="male|female|other")
    contact_phone: Optional[str] = None
    contact_email: Optional[EmailStr] = None
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


class ClientOut(ClientBase):
    id: int
    owner_id: Optional[int] = None  # Made optional for global clients
    created_at: datetime

    class Config:
        from_attributes = True


class BasicMedicalInfo(BaseModel):
    """Базовая медицинская информация - только для healthcare специалистов."""
    allergies: Optional[str] = None
    chronic_diseases: Optional[str] = None
    pregnancy: Optional[bool] = None
    
    class Config:
        from_attributes = True


class ClientSearchOut(BaseModel):
    """
    Схема для глобального поиска клиента.
    Содержит ограниченную информацию в зависимости от типа специалиста:
    - Beauty/Wellness: только базовая информация
    - Healthcare: базовая информация + базовые медданные (аллергии, хронические заболевания, беременность)
    Визиты и процедуры из других бизнесов НЕ включаются.
    """
    id: int
    full_name: str
    birth_date: Optional[date] = None
    gender: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    # Базовые медданные - только для healthcare специалистов
    basic_medical_info: Optional[BasicMedicalInfo] = None
    # Флаг - есть ли связь с текущим бизнесом
    is_linked_to_my_business: bool = False
    created_at: datetime
    
    class Config:
        from_attributes = True

