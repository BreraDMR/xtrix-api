"""Password reset contracts."""
from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas.base import InputModel
from app.schemas.errors import invalid


class PasswordResetRequest(InputModel):
    email: EmailStr = Field(..., max_length=254)


class PasswordResetRequestOut(BaseModel):
    message: str


class PasswordResetConfirm(InputModel):
    token: str = Field(..., min_length=20, max_length=512)
    new_password: str = Field(..., min_length=8)

    @field_validator("new_password")
    @classmethod
    def validate_password(cls, value):
        from app.api.core.security import validate_password_strength

        is_valid, error_msg = validate_password_strength(value)
        if not is_valid:
            raise invalid("weak_password", error_msg)
        return value


class PasswordResetConfirmOut(BaseModel):
    message: str
