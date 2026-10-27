"""Explicit recipient and private proof for a salon's visitor invitation."""
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.db.models.visitor_account import canonical_visitor_email
from app.schemas.base import InputModel
from app.schemas.datetimes import UtcDatetime


class ClientInviteIssueIn(InputModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(max_length=320)

    @field_validator("email")
    @classmethod
    def canonical_email(cls, value: str) -> str:
        return canonical_visitor_email(value)


class ClientInviteIssueOut(BaseModel):
    token: str
    expires_at: UtcDatetime
