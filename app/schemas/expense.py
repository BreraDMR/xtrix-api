from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class ExpenseBase(BaseModel):
    category_id: Optional[int] = None
    amount: float = Field(..., gt=0)
    currency: str = Field(default="EUR", max_length=3)
    description: Optional[str] = None
    expense_date: datetime
    payment_method: str = Field(default="other", pattern="^(cash|card|transfer|other)$")


class ExpenseCreate(ExpenseBase):
    business_id: int


class ExpenseUpdate(BaseModel):
    category_id: Optional[int] = None
    amount: Optional[float] = Field(None, gt=0)
    currency: Optional[str] = Field(None, max_length=3)
    description: Optional[str] = None
    expense_date: Optional[datetime] = None
    payment_method: Optional[str] = Field(None, pattern="^(cash|card|transfer|other)$")


class ExpenseOut(ExpenseBase):
    id: int
    business_id: int
    created_by_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    category: Optional["ExpenseCategoryOut"] = None

    class Config:
        from_attributes = True


# Для избежания циклических импортов
from app.schemas.expense_category import ExpenseCategoryOut
ExpenseOut.model_rebuild()

