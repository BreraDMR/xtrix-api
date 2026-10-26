"""The visitor can read only their own account's public security state."""
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.db.models.visitor_account import canonical_visitor_email
from app.schemas.base import InputModel
from app.schemas.datetimes import UtcDatetime


class VisitorAccountOut(BaseModel):
    id: int
    email: str
    email_verified_at: UtcDatetime
    created_at: UtcDatetime
    has_password: bool
    google_connected: bool


class VisitorLoginIn(InputModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1, max_length=320)
    password: SecretStr = Field(min_length=1, max_length=128, repr=False)

    @field_validator("email")
    @classmethod
    def canonical_email(cls, value: str) -> str:
        return canonical_visitor_email(value)


class VisitorLoginOut(BaseModel):
    session_token: str
    expires_at: UtcDatetime


class VisitorRegistrationIn(InputModel):
    model_config = ConfigDict(extra="forbid")

    token: SecretStr = Field(min_length=1, max_length=128, repr=False)
    new_password: SecretStr = Field(min_length=1, max_length=128, repr=False)


class VisitorGoogleNonceIn(InputModel):
    model_config = ConfigDict(extra="forbid")


class VisitorGoogleLoginIn(InputModel):
    model_config = ConfigDict(extra="forbid")

    credential: SecretStr = Field(min_length=1, max_length=16384, repr=False)
    nonce: SecretStr = Field(min_length=1, max_length=128, repr=False)
