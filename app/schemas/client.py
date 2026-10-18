from datetime import date
from typing import Optional
from app.schemas.limits import ClientLanguage
from pydantic import ConfigDict, BaseModel, Field, EmailStr, field_validator
from app.schemas.datetimes import UtcDatetime
from app.schemas.errors import invalid, invalid_choice
from app.schemas.base import InputModel
class ClientBase(BaseModel):
    full_name: str = Field(..., max_length=255, min_length=1)
    birth_date: Optional[date] = None
    gender: Optional[str] = Field(None, description="male|female|other")
    contact_phone: Optional[str] = None
    contact_email: Optional[EmailStr] = None
    location_lat: Optional[float] = Field(None, ge=-90, le=90)
    location_lon: Optional[float] = Field(None, ge=-180, le=180)
    language: ClientLanguage = Field("ru", description="Language of messages sent to the client: en, ru, cz")
    @field_validator('gender')
    @classmethod
    def validate_gender(cls, v):
        if v is not None and v not in ['male', 'female', 'other']:
            raise invalid_choice(['male', 'female', 'other'], "Gender")
        return v
    @field_validator('contact_phone')
    @classmethod
    def validate_phone(cls, v):
        if v is not None and v != "":
            from app.api.core.security import validate_phone
            is_valid, error_msg = validate_phone(v)
            if not is_valid:
                raise invalid("invalid_phone", error_msg)
        return v
    @field_validator('contact_email')
    @classmethod
    def validate_email(cls, v):
        if v is not None and v != "":
            from app.api.core.security import validate_email
            is_valid, error_msg = validate_email(v)
            if not is_valid:
                raise invalid("invalid_email", error_msg)
        return v
    @field_validator('birth_date')
    @classmethod
    def validate_birth_date(cls, v):
        if v is not None:
            from app.api.core.date_validation import validate_birth_date
            is_valid, error_msg = validate_birth_date(v)
            if not is_valid:
                raise invalid("invalid_birth_date", error_msg)
        return v
class ClientCreate(InputModel, ClientBase):
    owner_id: Optional[int] = None  
class ClientUpdate(InputModel):
    full_name: Optional[str] = Field(None, max_length=255, min_length=1)
    birth_date: Optional[date] = None
    gender: Optional[str] = Field(None, description="male|female|other")
    contact_phone: Optional[str] = None
    contact_email: Optional[EmailStr] = None
    location_lat: Optional[float] = Field(None, ge=-90, le=90)
    location_lon: Optional[float] = Field(None, ge=-180, le=180)
    language: Optional[ClientLanguage] = None
    @field_validator('gender')
    @classmethod
    def validate_gender(cls, v):
        if v is not None and v not in ['male', 'female', 'other']:
            raise invalid_choice(['male', 'female', 'other'], "Gender")
        return v
    @field_validator('contact_phone')
    @classmethod
    def validate_phone(cls, v):
        if v is not None and v != "":
            from app.api.core.security import validate_phone
            is_valid, error_msg = validate_phone(v)
            if not is_valid:
                raise invalid("invalid_phone", error_msg)
        return v
    @field_validator('contact_email')
    @classmethod
    def validate_email(cls, v):
        if v is not None and v != "":
            from app.api.core.security import validate_email
            is_valid, error_msg = validate_email(v)
            if not is_valid:
                raise invalid("invalid_email", error_msg)
        return v
    @field_validator('birth_date')
    @classmethod
    def validate_birth_date(cls, v):
        if v is not None:
            from app.api.core.date_validation import validate_birth_date
            is_valid, error_msg = validate_birth_date(v)
            if not is_valid:
                raise invalid("invalid_birth_date", error_msg)
        return v
class ClientOut(ClientBase):
    id: int
    owner_id: Optional[int] = None  
    created_at: UtcDatetime
    model_config = ConfigDict(from_attributes=True)
class BasicMedicalInfo(BaseModel):
    allergies: Optional[str] = None
    chronic_diseases: Optional[str] = None
    pregnancy: Optional[bool] = None
    model_config = ConfigDict(from_attributes=True)
class ClientSearchOut(BaseModel):
    id: int
    full_name: str
    birth_date: Optional[date] = None
    gender: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    basic_medical_info: Optional[BasicMedicalInfo] = None
    is_linked_to_my_business: bool = False
    created_at: UtcDatetime
    model_config = ConfigDict(from_attributes=True)
