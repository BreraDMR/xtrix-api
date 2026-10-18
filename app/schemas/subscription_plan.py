from typing import Optional
from pydantic import ConfigDict, BaseModel, Field
from app.schemas.base import InputModel
class SubscriptionPlanBase(BaseModel):
    code: str = Field(..., max_length=50)
    name: str = Field(..., max_length=255)
    period_days: int = Field(..., ge=1)
    price: float
    currency: str = "usd"
    features: Optional[str] = None
    is_active: bool = True
    is_trial: bool = Field(default=False, description="Is this a trial plan")
    trial_days: Optional[int] = Field(None, ge=1, le=30, description="Trial length in days (7-14 for solo, 3-5 for admin)")
class SubscriptionPlanCreate(InputModel, SubscriptionPlanBase):
    pass
class SubscriptionPlanUpdate(InputModel):
    name: Optional[str] = None
    period_days: Optional[int] = None
    price: Optional[float] = None
    currency: Optional[str] = None
    features: Optional[str] = None
    is_active: Optional[bool] = None
    is_trial: Optional[bool] = None
    trial_days: Optional[int] = None
class SubscriptionPlanOut(SubscriptionPlanBase):
    id: int
    model_config = ConfigDict(from_attributes=True)
