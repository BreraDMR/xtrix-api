from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

from app.schemas.datetimes import UtcDatetime


class BusinessAuditEventOut(BaseModel):
    id: int
    action: str
    entity_type: str
    entity_id: int
    actor_user_id: Optional[int] = None
    actor_name: Optional[str] = None
    actor_role: Optional[str] = None
    request_id: Optional[str] = None
    changes: Optional[dict[str, Any]] = None
    created_at: UtcDatetime

    model_config = ConfigDict(from_attributes=True)
