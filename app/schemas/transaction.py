from typing import Optional
from pydantic import ConfigDict, BaseModel, Field
from app.schemas.datetimes import UtcDatetime
class TransactionBase(BaseModel):
    subscription_id: int
    amount: float = Field(..., gt=0, description="Transaction amount")
    currency: str = Field(default="eur", description="Currency code")
    payment_method: str = Field(..., description="Payment method: card, apple_pay, google_pay, erl_pay")
    status: str = Field(default="pending", description="Transaction status: pending, succeeded, failed, refunded, cancelled")
    transaction_date: Optional[UtcDatetime] = None
    external_transaction_id: Optional[str] = None
    description: Optional[str] = None
    provider: Optional[str] = None
class TransactionCreate(TransactionBase):
    pass
class TransactionUpdate(BaseModel):
    status: Optional[str] = None
    description: Optional[str] = None
class TransactionOut(TransactionBase):
    id: int
    created_at: UtcDatetime
    updated_at: UtcDatetime
    model_config = ConfigDict(from_attributes=True)
