from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class SubscriptionBase(BaseModel):
    user_id: int
    plan_id: int
    provider: Optional[str] = None
    external_id: Optional[str] = None
    status: Optional[str] = "active"
    current_period_end: Optional[datetime] = None
    is_trial: bool = False
    trial_start: Optional[datetime] = None
    trial_end: Optional[datetime] = None


class SubscriptionCreate(SubscriptionBase):
    pass


class SubscriptionUpdate(BaseModel):
    status: Optional[str] = None
    current_period_end: Optional[datetime] = None
    external_id: Optional[str] = None


class SubscriptionOut(SubscriptionBase):
    id: int
    created_at: datetime
    # trial_start и trial_end уже включены в SubscriptionBase

    class Config:
        from_attributes = True




