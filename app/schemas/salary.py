from typing import Optional
from app.schemas.limits import MAX_MONEY, LongText, PaymentMethod, PaymentStatus, PositiveMoney
from pydantic import ConfigDict, BaseModel, Field
from app.schemas.datetimes import UtcDatetime
class SalaryCalculationBase(BaseModel):
    employee_id: int = Field(..., description="Employee ID")
    period_start: UtcDatetime = Field(..., description="Period start")
    period_end: UtcDatetime = Field(..., description="Period end")
    notes: Optional[LongText] = Field(None, description="Calculation notes")
class SalaryCalculationCreate(SalaryCalculationBase):
    pass
class SalaryCalculationUpdate(BaseModel):
    is_paid: Optional[bool] = Field(None, description="Mark as paid")
    notes: Optional[LongText] = Field(None, description="Calculation notes")
class SalaryCalculationOut(SalaryCalculationBase):
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
class CalculateSalaryRequest(BaseModel):
    period_start: UtcDatetime = Field(..., description="Period start")
    period_end: UtcDatetime = Field(..., description="Period end")
    notes: Optional[LongText] = Field(None, description="Calculation notes")
class SalaryAdjustmentBase(BaseModel):
    type: str = Field(..., description="bonus, penalty, overtime, allowance, deduction")
    amount: float = Field(..., ge=-MAX_MONEY, le=MAX_MONEY, description="Can be negative (penalty)")
    description: Optional[LongText] = Field(None, description="Description")
class SalaryAdjustmentCreate(SalaryAdjustmentBase):
    pass
class SalaryAdjustmentOut(SalaryAdjustmentBase):
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
class SalaryPaymentCreate(SalaryPaymentBase):
    pass
class SalaryPaymentUpdate(BaseModel):
    amount: Optional[PositiveMoney] = None
    payment_date: Optional[UtcDatetime] = None
    payment_method: Optional[PaymentMethod] = None
    status: Optional[PaymentStatus] = None
    notes: Optional[LongText] = None
class SalaryPaymentOut(SalaryPaymentBase):
    id: int
    salary_calculation_id: int
    created_by_id: int
    created_at: UtcDatetime
    updated_at: UtcDatetime
    model_config = ConfigDict(from_attributes=True)
class SalaryCalculationDetail(SalaryCalculationOut):
    adjustments: list[SalaryAdjustmentOut] = Field(default_factory=list, description="Bonuses/penalties")
    payments: list[SalaryPaymentOut] = Field(default_factory=list, description="Payments")
