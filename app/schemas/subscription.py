from typing import Optional
from pydantic import ConfigDict, BaseModel, Field
from app.schemas.datetimes import UtcDatetime
class SubscriptionBase(BaseModel):
    user_id: int
    plan_id: int
    provider: Optional[str] = None
    external_id: Optional[str] = None
    status: Optional[str] = "active"
    current_period_end: Optional[UtcDatetime] = None
    is_trial: bool = False
    trial_start: Optional[UtcDatetime] = None
    trial_end: Optional[UtcDatetime] = None
    cancel_at_period_end: bool = False
    cancelled_at: Optional[UtcDatetime] = None
    reactivated_at: Optional[UtcDatetime] = None
    last_transaction_id: Optional[int] = None
class SubscriptionCreate(SubscriptionBase):
    pass
class SubscriptionUpdate(BaseModel):
    status: Optional[str] = None
    current_period_end: Optional[UtcDatetime] = None
    external_id: Optional[str] = None
    cancel_at_period_end: Optional[bool] = None
    cancelled_at: Optional[UtcDatetime] = None
    reactivated_at: Optional[UtcDatetime] = None
    last_transaction_id: Optional[int] = None
class SubscriptionOut(SubscriptionBase):
    id: int
    created_at: UtcDatetime
    model_config = ConfigDict(from_attributes=True)
class SubscriptionCancelRequest(BaseModel):
    cancel_at_period_end: bool = Field(default=True, description="Cancel at end of current period (true) or immediately (false)")
class SubscriptionReactivateRequest(BaseModel):
    plan_id: Optional[int] = Field(None, description="Optional: change plan when reactivating")
