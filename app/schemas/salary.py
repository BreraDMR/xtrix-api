from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field
class SalaryCalculationBase(BaseModel):
    employee_id: int = Field(..., description="ID сотрудника (Employee)")
    period_start: datetime = Field(..., description="Начало периода расчета")
    period_end: datetime = Field(..., description="Конец периода расчета")
    notes: Optional[str] = Field(None, description="Заметки к расчету")
class SalaryCalculationCreate(SalaryCalculationBase):
    pass
class SalaryCalculationUpdate(BaseModel):
    is_paid: Optional[bool] = Field(None, description="Отметить как выплаченную")
    notes: Optional[str] = Field(None, description="Заметки к расчету")
class SalaryCalculationOut(SalaryCalculationBase):
    id: int
    total_visits: int = Field(default=0, description="Общее количество визитов")
    completed_visits: int = Field(default=0, description="Завершенные визиты")
    cancelled_visits: int = Field(default=0, description="Отмененные визиты")
    total_revenue: float = Field(default=0.0, description="Общая сумма визитов")
    commission_percentage: Optional[float] = Field(None, description="Процент комиссии")
    commission_amount: float = Field(default=0.0, description="Сумма комиссии")
    base_salary: float = Field(default=0.0, description="Фиксированная часть зарплаты")
    total_salary: float = Field(default=0.0, description="Итоговая зарплата")
    adjustments_total: float = Field(default=0.0, description="Сумма всех бонусов/штрафов")
    total_with_adjustments: float = Field(default=0.0, description="Итоговая зарплата с учетом бонусов/штрафов")
    paid_amount: float = Field(default=0.0, description="Сумма уже выплаченная")
    payment_status: str = Field(default="unpaid", description="Статус выплаты: unpaid, partial, paid")
    is_paid: bool = Field(default=False, description="Выплачена ли зарплата (deprecated)")
    paid_at: Optional[datetime] = Field(None, description="Дата последней выплаты")
    calculated_at: datetime
    calculated_by_id: Optional[int] = Field(None, description="ID пользователя, который рассчитал зарплату")
    created_at: datetime
    updated_at: datetime
    class Config:
        from_attributes = True
class SalaryCalculationWithEmployee(SalaryCalculationOut):
    employee: Optional[dict] = Field(None, description="Информация о сотруднике")
class CalculateSalaryRequest(BaseModel):
    period_start: datetime = Field(..., description="Начало периода расчета")
    period_end: datetime = Field(..., description="Конец периода расчета")
    notes: Optional[str] = Field(None, description="Заметки к расчету")
class SalaryAdjustmentBase(BaseModel):
    type: str = Field(..., description="Тип: bonus, penalty, overtime, allowance, deduction")
    amount: float = Field(..., description="Сумма (может быть отрицательной)")
    description: Optional[str] = Field(None, description="Описание")
class SalaryAdjustmentCreate(SalaryAdjustmentBase):
    pass
class SalaryAdjustmentOut(SalaryAdjustmentBase):
    id: int
    salary_calculation_id: int
    created_by_id: int
    created_at: datetime
    class Config:
        from_attributes = True
class SalaryPaymentBase(BaseModel):
    amount: float = Field(..., description="Сумма выплаты")
    payment_date: datetime = Field(..., description="Дата выплаты")
    payment_method: str = Field(default="other", description="Способ оплаты: cash, card, transfer, other")
    status: str = Field(default="completed", description="Статус: completed, pending, failed")
    notes: Optional[str] = Field(None, description="Заметки")
class SalaryPaymentCreate(SalaryPaymentBase):
    pass
class SalaryPaymentUpdate(BaseModel):
    amount: Optional[float] = None
    payment_date: Optional[datetime] = None
    payment_method: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None
class SalaryPaymentOut(SalaryPaymentBase):
    id: int
    salary_calculation_id: int
    created_by_id: int
    created_at: datetime
    updated_at: datetime
    class Config:
        from_attributes = True
class SalaryCalculationDetail(SalaryCalculationOut):
    adjustments: list[SalaryAdjustmentOut] = Field(default_factory=list, description="Список бонусов/штрафов")
    payments: list[SalaryPaymentOut] = Field(default_factory=list, description="Список выплат")
