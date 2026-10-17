from app.schemas.datetimes import UtcDatetime
from typing import Optional
from pydantic import ConfigDict, BaseModel, Field
from app.schemas.limits import Currency, LongText, PositiveMoney
class ExpenseBase(BaseModel):
    category_id: Optional[int] = None
    amount: PositiveMoney
    currency: Currency = "EUR"
    description: Optional[LongText] = None
    expense_date: UtcDatetime
    payment_method: str = Field(default="other", pattern="^(cash|card|transfer|other)$")
class ExpenseCreate(ExpenseBase):
    business_id: int
class ExpenseUpdate(BaseModel):
    category_id: Optional[int] = None
    amount: Optional[PositiveMoney] = None
    currency: Optional[Currency] = None
    description: Optional[LongText] = None
    expense_date: Optional[UtcDatetime] = None
    payment_method: Optional[str] = Field(None, pattern="^(cash|card|transfer|other)$")
class ExpenseOut(ExpenseBase):
    id: int
    business_id: int
    created_by_id: Optional[int] = None
    created_at: UtcDatetime
    updated_at: UtcDatetime
    category: Optional["ExpenseCategoryOut"] = None
    model_config = ConfigDict(from_attributes=True)
from app.schemas.expense_category import ExpenseCategoryOut
ExpenseOut.model_rebuild()
