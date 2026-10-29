from decimal import Decimal
from typing import Optional
from app.schemas.limits import MAX_MONEY, LongText, PaymentMethod, PaymentStatus, PositiveMoney
from pydantic import ConfigDict, BaseModel, Field
from app.schemas.datetimes import UtcDatetime
from app.schemas.base import InputModel
class SalaryPeriodOut(BaseModel):
    id: int
    business_id: int
    employee_id: int
    period_start: UtcDatetime
    period_end: UtcDatetime
    base_salary_currency: Optional[str] = None
    base_salary_exact: Decimal
    commission_percentage: Decimal
    created_at: UtcDatetime
    created_by_id: Optional[int] = None
    model_config = ConfigDict(from_attributes=True)

class SalaryCalculationBase(BaseModel):
    employee_id: int = Field(..., description="Employee ID")
    period_start: UtcDatetime = Field(..., description="Period start")
    period_end: UtcDatetime = Field(..., description="Period end")
    notes: Optional[LongText] = Field(None, description="Calculation notes")
class SalaryCalculationCreate(SalaryCalculationBase):
    pass
class SalaryCalculationUpdate(InputModel):
    is_paid: Optional[bool] = Field(None, description="Mark as paid")
    notes: Optional[LongText] = Field(None, description="Calculation notes")
class SalaryCalculationOut(SalaryCalculationBase):
    salary_period_id: Optional[int] = None
    salary_period: Optional[SalaryPeriodOut] = None
    currency: Optional[str] = None
    business_id_snapshot: Optional[int] = None
    total_revenue_exact: Optional[Decimal] = None
    commission_amount_exact: Optional[Decimal] = None
    base_salary_exact: Optional[Decimal] = None
    total_salary_exact: Optional[Decimal] = None
    adjustments_total_exact: Optional[Decimal] = None
    total_with_adjustments_exact: Optional[Decimal] = None
    paid_amount_exact: Optional[Decimal] = None
    id: int
    total_visits: int = Field(default=0, description="Total visits")
    completed_visits: int = Field(default=0, description="Completed visits")
    cancelled_visits: int = Field(default=0, description="Cancelled visits")
    total_revenue: float = Field(default=0.0, description="Total visit revenue")
    commission_percentage: Optional[float] = Field(None, description="Commission percentage")
    commission_amount: float = Field(default=0.0, description="Commission amount")
    base_salary: float = Field(default=0.0, description="Fixed part of the salary")
    total_salary: float = Field(default=0.0, description="Total salary")
    adjustments_total: float = Field(default=0.0, description="Sum of bonuses/penalties")
    total_with_adjustments: float = Field(default=0.0, description="Total salary incl. bonuses/penalties")
    paid_amount: float = Field(default=0.0, description="Amount already paid")
    payment_status: str = Field(default="unpaid", description="Payment status: unpaid, partial, paid")
    is_paid: bool = Field(default=False, description="Is the salary paid (deprecated)")
    paid_at: Optional[UtcDatetime] = Field(None, description="Date of the last payment")
    calculated_at: UtcDatetime
    calculated_by_id: Optional[int] = Field(None, description="ID of the user who calculated it")
    created_at: UtcDatetime
    updated_at: UtcDatetime
    model_config = ConfigDict(from_attributes=True)
class SalaryCalculationWithEmployee(SalaryCalculationOut):
    employee: Optional[dict] = Field(None, description="Employee info")
class CalculateSalaryRequest(InputModel):
    currency: Optional[str] = Field(None, pattern=r"^[A-Z]{3}$", min_length=3, max_length=3)
    period_start: UtcDatetime = Field(..., description="Period start")
    period_end: UtcDatetime = Field(..., description="Period end")
    notes: Optional[LongText] = Field(None, description="Calculation notes")
class SalaryAdjustmentBase(BaseModel):
    type: str = Field(..., description="bonus, penalty, overtime, allowance, deduction")
    amount: float = Field(..., ge=-MAX_MONEY, le=MAX_MONEY, description="Can be negative (penalty)")
    description: Optional[LongText] = Field(None, description="Description")
class SalaryAdjustmentCreate(InputModel, SalaryAdjustmentBase):
    pass
class SalaryAdjustmentOut(SalaryAdjustmentBase):
    currency: Optional[str] = None
    amount_exact: Optional[Decimal] = None
    id: int
    salary_calculation_id: int
    created_by_id: int
    created_at: UtcDatetime
    model_config = ConfigDict(from_attributes=True)
class SalaryPaymentBase(BaseModel):
    amount: PositiveMoney = Field(..., description="Payment amount")
    payment_date: UtcDatetime = Field(..., description="Payment date")
    payment_method: PaymentMethod = Field(default="other")
    status: PaymentStatus = Field(default="completed")
    notes: Optional[LongText] = Field(None, description="Notes")
class SalaryPaymentCreate(InputModel, SalaryPaymentBase):
    pass
class SalaryPaymentUpdate(InputModel):
    amount: Optional[PositiveMoney] = None
    payment_date: Optional[UtcDatetime] = None
    payment_method: Optional[PaymentMethod] = None
    status: Optional[PaymentStatus] = None
    notes: Optional[LongText] = None
class SalaryPaymentOut(SalaryPaymentBase):
    currency: Optional[str] = None
    amount_exact: Optional[Decimal] = None
    id: int
    salary_calculation_id: int
    created_by_id: int
    created_at: UtcDatetime
    updated_at: UtcDatetime
    model_config = ConfigDict(from_attributes=True)
class SalaryCalculationDetail(SalaryCalculationOut):
    adjustments: list[SalaryAdjustmentOut] = Field(default_factory=list, description="Bonuses/penalties")
    payments: list[SalaryPaymentOut] = Field(default_factory=list, description="Payments")
