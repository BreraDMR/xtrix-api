from typing import Literal

from pydantic import BaseModel, Field

from app.db.models.specialist_type import SpecialistType
from app.schemas.base import InputModel


class GoogleConfigOut(BaseModel):
    enabled: bool
    client_id: str | None


class GoogleNonceOut(BaseModel):
    nonce: str
    expires_in: int


class GoogleLoginIn(InputModel):
    credential: str = Field(min_length=1, max_length=16384)
    nonce: str = Field(min_length=32, max_length=128)
    role: Literal["business_owner", "specialist"] = "business_owner"
    link_existing: bool = False
    password: str | None = Field(default=None, max_length=128)
    totp_token: str | None = Field(default=None, min_length=6, max_length=6)
    # only used when this sign-in creates the account; an existing account keeps its own
    specialist_type: SpecialistType | None = None
