from typing import Optional
from pydantic import ConfigDict, BaseModel, EmailStr, Field, field_validator
from app.schemas.datetimes import UtcDatetime
from app.schemas.errors import invalid, invalid_choice
from app.schemas.base import InputModel
class UserBase(BaseModel):
    full_name: str = Field(..., max_length=255, min_length=1)
    phone: Optional[str] = Field(None, max_length=50)
    email: Optional[EmailStr] = None
    role: str = Field(..., description="admin|business_owner|specialist|employee|client")
    specialist_type: Optional[str] = Field(None, max_length=64, description="Type of specialist (only for role='specialist')")
    @field_validator('phone')
    @classmethod
    def validate_phone(cls, v):
        if v is not None and v != "":
            from app.api.core.security import validate_phone
            is_valid, error_msg = validate_phone(v)
            if not is_valid:
                raise invalid("invalid_phone", error_msg)
        return v
    @field_validator('email')
    @classmethod
    def validate_email(cls, v):
        if v is not None and v != "":
            from app.api.core.security import validate_email
            is_valid, error_msg = validate_email(v)
            if not is_valid:
                raise invalid("invalid_email", error_msg)
        return v
    @field_validator('role')
    @classmethod
    def validate_role(cls, v):
        allowed_roles = ['admin', 'business_owner', 'specialist', 'employee', 'client']
        if v not in allowed_roles:
            raise invalid_choice(allowed_roles, "Role")
        return v
    @field_validator('specialist_type')
    @classmethod
    def validate_specialist_type(cls, v):
        return v
class UserCreate(InputModel, UserBase):
    password: str = Field(..., min_length=8)
    business_id: Optional[int] = Field(
        None, 
        description="Business to attach the specialist to (optional, only for role='specialist' or 'employee')"
    )
    @field_validator('password')
    @classmethod
    def validate_password(cls, v):
        from app.api.core.security import validate_password_strength
        is_valid, error_msg = validate_password_strength(v)
        if not is_valid:
            raise invalid("weak_password", error_msg)
        return v
class UserLogin(BaseModel):
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    password: str
    totp_token: Optional[str] = Field(None, min_length=6, max_length=6, description="6-digit TOTP code for 2FA")
class UserOut(UserBase):
    id: int
    is_2fa_enabled: bool = False
    moderation_status: Optional[str] = Field(default="pending", description="Moderation status: pending, approved, rejected")
    moderation_notes: Optional[str] = None
    moderated_at: Optional[UtcDatetime] = None
    moderated_by_id: Optional[int] = None
    business_id: Optional[int] = Field(None, description="Business the user works in: their own, or the employer's for staff")
    employer_id: Optional[int] = Field(None, description="User id of the owner who employs this user; null for owners and solo specialists")
    model_config = ConfigDict(from_attributes=True)
class TwoFactorSetup(BaseModel):
    secret: str
    qr_code: str
class TwoFactorVerify(BaseModel):
    token: str = Field(..., min_length=6, max_length=6, description="6-digit TOTP code")
class TwoFactorEnable(BaseModel):
    token: str = Field(..., min_length=6, max_length=6, description="6-digit TOTP code to verify before enabling")
class LoginResponse(BaseModel):
    requires_2fa: bool = False
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str = "bearer"
class ModerationRequest(InputModel):
    status: str = Field(..., description="Moderation status: approved or rejected")
    notes: Optional[str] = Field(None, max_length=1000, description="Moderator's comment")
    @field_validator('status')
    @classmethod
    def validate_status(cls, v):
        allowed_statuses = ['approved', 'rejected']
        if v not in allowed_statuses:
            raise invalid_choice(allowed_statuses, "Status")
        return v
class ModerationResponse(BaseModel):
    id: int
    moderation_status: str
    moderation_notes: Optional[str] = None
    moderated_at: Optional[UtcDatetime] = None
    moderated_by_id: Optional[int] = None
    model_config = ConfigDict(from_attributes=True)
