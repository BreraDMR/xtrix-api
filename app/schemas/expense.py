from app.schemas.datetimes import UtcDatetime
from datetime import date
from typing import List, Optional
from pydantic import ConfigDict, BaseModel, Field
from app.schemas.limits import Currency, LongText, PositiveMoney
from app.schemas.base import InputModel
class ExpenseBase(BaseModel):
    category_id: Optional[int] = None
    amount: PositiveMoney
    currency: Currency = "EUR"
    description: Optional[LongText] = None
    expense_date: UtcDatetime
    payment_method: str = Field(default="other", pattern="^(cash|card|transfer|other)$")
class ExpenseCreate(InputModel, ExpenseBase):
    business_id: int
class ExpenseUpdate(InputModel):
    category_id: Optional[int] = None
    amount: Optional[PositiveMoney] = None
    currency: Optional[Currency] = None
    description: Optional[LongText] = None
    expense_date: Optional[UtcDatetime] = None
    payment_method: Optional[str] = Field(None, pattern="^(cash|card|transfer|other)$")
class ExpenseOut(ExpenseBase):
    id: int
    currency: str  # whatever is stored, older rows predate the ISO check
    business_id: int
    created_by_id: Optional[int] = None
    source: Optional[str] = Field(None, description="null for manual expenses, salary_payment for automatic ones")
    employee_id: Optional[int] = Field(None, description="Employee paid, when source is salary_payment")
    created_at: UtcDatetime
    updated_at: UtcDatetime
    category: Optional["ExpenseCategoryOut"] = None
    model_config = ConfigDict(from_attributes=True)
class ExpenseCategoryTotal(BaseModel):
    category_id: int
    category_name: str
    category_code: Optional[str] = None
    color: Optional[str] = None
    total: float
class ProfitReportPeriod(BaseModel):
    # "from" is a python keyword, hence the alias
    from_: UtcDatetime = Field(..., alias="from")
    to: UtcDatetime
class ProfitDay(BaseModel):
    date: date
    revenue: float
    expenses: float
    profit: float
class ProfitReportResponse(BaseModel):
    period: ProfitReportPeriod
    revenue: float
    expenses: float
    profit: float
    profit_margin: float = Field(..., description="Profit as a percent of revenue, 0 when there is no revenue")
    expenses_by_category: List[ExpenseCategoryTotal]
    daily: List[ProfitDay]
from app.schemas.expense_category import ExpenseCategoryOut
ExpenseOut.model_rebuild()
