"""The visitor can read only their own account's public security state."""
from pydantic import BaseModel

from app.schemas.datetimes import UtcDatetime


class VisitorAccountOut(BaseModel):
    id: int
    email: str
    email_verified_at: UtcDatetime
    created_at: UtcDatetime
    has_password: bool
    google_connected: bool
