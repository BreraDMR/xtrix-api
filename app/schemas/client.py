from datetime import date
from typing import Literal, Optional
from app.schemas.limits import ClientLanguage
from pydantic import ConfigDict, BaseModel, Field, EmailStr, field_validator, model_validator
from app.schemas.datetimes import UtcDatetime
from app.schemas.errors import invalid, invalid_choice
from app.schemas.base import InputModel
class ClientContactIn(InputModel):
    kind: Literal["phone", "email"]
    value: str = Field(..., min_length=1, max_length=255)
    label: Optional[str] = Field(None, max_length=50, description="Free text like 'work' or 'mum'")
    is_primary: bool = False
    notify: bool = Field(True, description="Reminders and other messages may go to this contact")

    @field_validator("label")
    @classmethod
    def clean_label(cls, v):
        if v is None or v == "":
            return None
        if any(ord(char) < 32 or ord(char) == 127 for char in v):
            raise invalid("invalid_label", "The label can't contain line breaks or control characters")
        return v

    @model_validator(mode="after")
    def check_value(self):
        from app.api.core import client_contacts
        from app.api.core.security import validate_email, validate_phone
        if self.kind == "phone":
            is_valid, error_msg = validate_phone(self.value)
            if not is_valid:
                raise invalid("invalid_phone", error_msg)
        else:
            is_valid, error_msg = validate_email(self.value)
            if not is_valid:
                raise invalid("invalid_email", error_msg)
        self.value = client_contacts.normalize(self.kind, self.value)
        return self


class ClientContactOut(BaseModel):
    id: int
    kind: Literal["phone", "email"]
    value: str
    label: Optional[str] = None
    is_primary: bool
    notify: bool
    created_at: UtcDatetime
    model_config = ConfigDict(from_attributes=True)


def _check_contacts_list(model):
    """contacts, when sent, is the whole list. The old single fields may come along but must agree."""
    if model.contacts is None:
        return model
    from app.api.core import client_contacts
    items = [client_contacts.ContactValue(c.kind, c.value, c.label, c.is_primary, c.notify)
             for c in model.contacts]
    try:
        items = client_contacts.check_contacts(items)
    except client_contacts.ContactsError as error:
        raise invalid(error.code, str(error))
    for kind, field in (("phone", "contact_phone"), ("email", "contact_email")):
        if field not in model.model_fields_set:
            continue
        primary = next((c.value for c in items if c.kind == kind and c.is_primary), None)
        if client_contacts.normalize(kind, getattr(model, field)) != primary:
            raise invalid("contacts_mismatch", f"{field} must match the primary {kind} in contacts")
    return model


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
    contacts: Optional[list[ClientContactIn]] = Field(
        None, max_length=8,
        description="Every phone (up to 5) and email (up to 3). When sent, it is the whole list and "
                    "contact_phone / contact_email follow its primary entries",
    )

    allow_shared_contact: bool = Field(
        False,
        description="Create the card even though another client already has one of these phones or "
                    "emails (a family sharing a number). Name + birth date duplicates are still refused",
    )

    @model_validator(mode="after")
    def contacts_agree(self):
        return _check_contacts_list(self)
class ClientUpdate(InputModel):
    full_name: Optional[str] = Field(None, max_length=255, min_length=1)
    birth_date: Optional[date] = None
    gender: Optional[str] = Field(None, description="male|female|other")
    contact_phone: Optional[str] = None
    contact_email: Optional[EmailStr] = None
    location_lat: Optional[float] = Field(None, ge=-90, le=90)
    location_lon: Optional[float] = Field(None, ge=-180, le=180)
    language: Optional[ClientLanguage] = None
    contacts: Optional[list[ClientContactIn]] = Field(
        None, max_length=8,
        description="Replaces every phone and email of the card. Leave it out to keep them",
    )

    @model_validator(mode="after")
    def contacts_agree(self):
        return _check_contacts_list(self)
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
    contacts: list[ClientContactOut] = []
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
    contacts: list[ClientContactOut] = []
    basic_medical_info: Optional[BasicMedicalInfo] = None
    is_linked_to_my_business: bool = False
    created_at: UtcDatetime
    model_config = ConfigDict(from_attributes=True)
