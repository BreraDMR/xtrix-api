from typing import Optional

from pydantic import BaseModel, Field


class SubscriptionPlanBase(BaseModel):
    code: str = Field(..., max_length=50)
    name: str = Field(..., max_length=255)
    period_days: int = Field(..., ge=1)
    price: float
    currency: str = "usd"
    features: Optional[str] = None
    is_active: bool = True


class SubscriptionPlanCreate(SubscriptionPlanBase):
    pass


class SubscriptionPlanUpdate(BaseModel):
    name: Optional[str] = None
    period_days: Optional[int] = None
    price: Optional[float] = None
    currency: Optional[str] = None
    features: Optional[str] = None
    is_active: Optional[bool] = None


class SubscriptionPlanOut(SubscriptionPlanBase):
    id: int

    class Config:
        from_attributes = True




