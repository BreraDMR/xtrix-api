from typing import Optional
import re

from pydantic import BaseModel, EmailStr, Field, field_validator


class UserBase(BaseModel):
    full_name: str = Field(..., max_length=255, min_length=1)
    phone: Optional[str] = Field(None, max_length=50)
    email: Optional[EmailStr] = None
    role: str = Field(..., description="admin|specialist|client")
    
    @field_validator('phone')
    @classmethod
    def validate_phone(cls, v):
        if v is not None and v != "":
            from app.api.core.security import validate_phone
            is_valid, error_msg = validate_phone(v)
            if not is_valid:
                raise ValueError(error_msg)
        return v
    
    @field_validator('email')
    @classmethod
    def validate_email(cls, v):
        if v is not None and v != "":
            from app.api.core.security import validate_email
            is_valid, error_msg = validate_email(v)
            if not is_valid:
                raise ValueError(error_msg)
        return v
    
    @field_validator('role')
    @classmethod
    def validate_role(cls, v):
        allowed_roles = ['admin', 'specialist', 'client']
        if v not in allowed_roles:
            raise ValueError(f"Role must be one of: {', '.join(allowed_roles)}")
        return v


class UserCreate(UserBase):
    password: str = Field(..., min_length=8)
    
    @field_validator('password')
    @classmethod
    def validate_password(cls, v):
        from app.api.core.security import validate_password_strength
        is_valid, error_msg = validate_password_strength(v)
        if not is_valid:
            raise ValueError(error_msg)
        return v


class UserLogin(BaseModel):
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    password: str
    totp_token: Optional[str] = Field(None, min_length=6, max_length=6, description="6-digit TOTP code for 2FA")


class UserOut(UserBase):
    id: int
    is_2fa_enabled: bool = False

    class Config:
        from_attributes = True


class TwoFactorSetup(BaseModel):
    secret: str
    qr_code: str
    

class TwoFactorVerify(BaseModel):
    token: str = Field(..., min_length=6, max_length=6, description="6-digit TOTP code")


class TwoFactorEnable(BaseModel):
    token: str = Field(..., min_length=6, max_length=6, description="6-digit TOTP code to verify before enabling")


class LoginResponse(BaseModel):
    """Response model for login that may require 2FA."""
    requires_2fa: bool = False
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str = "bearer"


class ForgotPasswordRequest(BaseModel):
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    
    @field_validator('phone')
    @classmethod
    def validate_phone(cls, v):
        if v is not None and v != "":
            from app.api.core.security import validate_phone
            is_valid, error_msg = validate_phone(v)
            if not is_valid:
                raise ValueError(error_msg)
        return v


class ResetPasswordRequest(BaseModel):
    token: Optional[str] = None  # Token from email link
    code: Optional[str] = None  # 6-digit code from SMS/email
    new_password: str = Field(..., min_length=8)
    
    @field_validator('new_password')
    @classmethod
    def validate_password(cls, v):
        from app.api.core.security import validate_password_strength
        is_valid, error_msg = validate_password_strength(v)
        if not is_valid:
            raise ValueError(error_msg)
        return v



