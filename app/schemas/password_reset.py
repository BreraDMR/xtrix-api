"""Password reset request contracts."""
from pydantic import BaseModel, EmailStr, Field

from app.schemas.base import InputModel


class PasswordResetRequest(InputModel):
    email: EmailStr = Field(..., max_length=254)


class PasswordResetRequestOut(BaseModel):
    message: str
