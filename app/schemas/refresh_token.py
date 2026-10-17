from pydantic import ConfigDict, BaseModel
from datetime import datetime
from typing import Optional
class RefreshTokenCreate(BaseModel):
    token: str
    user_id: int
    expires_at: datetime
class RefreshTokenOut(BaseModel):
    id: int
    token: str
    user_id: int
    expires_at: datetime
    is_revoked: bool
    created_at: datetime
    revoked_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)
class TokenRefresh(BaseModel):
    refresh_token: str
class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
