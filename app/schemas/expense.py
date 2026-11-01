from app.schemas.datetimes import UtcDatetime
from datetime import date
from decimal import Decimal
from typing import Literal
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
    salary_payment_id: Optional[int] = None
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
class ExpenseCurrencyTotal(BaseModel):
    currency: Optional[str] = None
    total: Optional[Decimal] = None
    count: int
    requires_review: bool = False

class ExpenseCategoryTotal(BaseModel):
    category_id: Optional[int] = None
    category_name: Optional[str] = None
    category_code: Optional[str] = None
    color: Optional[str] = None
    total: Optional[float] = None
    currency: Optional[str] = None
    requires_review: bool = False
    by_currency: List[ExpenseCurrencyTotal] = Field(default_factory=list)

class ProfitCurrencySlice(BaseModel):
    currency: Optional[str] = None
    revenue: Optional[Decimal] = None
    expenses: Optional[Decimal] = None
    profit: Optional[Decimal] = None
    profit_margin: Optional[float] = None
    quote_count: int
    expense_count: int
    requires_review: bool = False

class ProfitReportPeriod(BaseModel):
    # "from" is a python keyword, hence the alias
    from_: UtcDatetime = Field(..., alias="from")
    to: UtcDatetime
class ProfitDay(BaseModel):
    currency: Optional[str] = None
    requires_review: bool = False
    by_currency: List[ProfitCurrencySlice] = Field(default_factory=list)
    date: date
    revenue: Optional[float] = None
    expenses: Optional[float] = None
    profit: Optional[float] = None
class ProfitReportResponse(BaseModel):
    value_basis: Literal["quoted_service_price"] = "quoted_service_price"
    currency: Optional[str] = None
    requires_review: bool = False
    by_currency: List[ProfitCurrencySlice] = Field(default_factory=list)
    period: ProfitReportPeriod
    revenue: Optional[float] = None
    expenses: Optional[float] = None
    profit: Optional[float] = None
    profit_margin: Optional[float] = Field(None, description="Profit as a percent of revenue, 0 when there is no revenue")
    expenses_by_category: List[ExpenseCategoryTotal]
    daily: List[ProfitDay]
from app.schemas.expense_category import ExpenseCategoryOut
ExpenseOut.model_rebuild()
