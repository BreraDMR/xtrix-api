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
    cancel_at_period_end: bool = False
    cancelled_at: Optional[datetime] = None
    reactivated_at: Optional[datetime] = None
    last_transaction_id: Optional[int] = None


class SubscriptionCreate(SubscriptionBase):
    pass


class SubscriptionUpdate(BaseModel):
    status: Optional[str] = None
    current_period_end: Optional[datetime] = None
    external_id: Optional[str] = None
    cancel_at_period_end: Optional[bool] = None
    cancelled_at: Optional[datetime] = None
    reactivated_at: Optional[datetime] = None
    last_transaction_id: Optional[int] = None


class SubscriptionOut(SubscriptionBase):
    id: int
    created_at: datetime
    # trial_start и trial_end уже включены в SubscriptionBase
    # cancel_at_period_end, cancelled_at, reactivated_at уже включены в SubscriptionBase

    class Config:
        from_attributes = True


class SubscriptionCancelRequest(BaseModel):
    cancel_at_period_end: bool = Field(default=True, description="Cancel at end of current period (true) or immediately (false)")


class SubscriptionReactivateRequest(BaseModel):
    plan_id: Optional[int] = Field(None, description="Optional: change plan when reactivating")




