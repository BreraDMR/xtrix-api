"""Delivery log contracts: metadata and errors, never the message body or its tokens."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class NotificationDeliveryOut(BaseModel):
    id: int
    visit_id: Optional[int] = None
    kind: str
    channel: str
    recipient: str
    status: str
    attempts: int
    provider: Optional[str] = None
    scheduled_at: datetime
    sent_at: Optional[datetime] = None
    last_error: Optional[str] = None
    retryable: bool
    created_at: datetime
    updated_at: datetime
