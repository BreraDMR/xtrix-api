from pydantic import ConfigDict, BaseModel
from typing import Optional
from app.schemas.datetimes import UtcDatetime
class RefreshTokenCreate(BaseModel):
    token: str
    user_id: int
    expires_at: UtcDatetime
class RefreshTokenOut(BaseModel):
    id: int
    token: str
    user_id: int
    expires_at: UtcDatetime
    is_revoked: bool
    created_at: UtcDatetime
    revoked_at: Optional[UtcDatetime] = None
    model_config = ConfigDict(from_attributes=True)
class TokenRefresh(BaseModel):
    refresh_token: str
class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
