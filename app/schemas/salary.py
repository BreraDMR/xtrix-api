from typing import Optional
from datetime import datetime

from pydantic import BaseModel, Field


class SalaryCalculationBase(BaseModel):
    """Базовая модель расчета зарплаты."""
    employee_id: int = Field(..., description="ID сотрудника (Employee)")
    period_start: datetime = Field(..., description="Начало периода расчета")
    period_end: datetime = Field(..., description="Конец периода расчета")
    notes: Optional[str] = Field(None, description="Заметки к расчету")


class SalaryCalculationCreate(SalaryCalculationBase):
    """Модель для создания расчета зарплаты."""
    pass


class SalaryCalculationUpdate(BaseModel):
    """Модель для обновления расчета зарплаты (только для отметки выплаты)."""
    is_paid: Optional[bool] = Field(None, description="Отметить как выплаченную")
    notes: Optional[str] = Field(None, description="Заметки к расчету")


class SalaryCalculationOut(SalaryCalculationBase):
    """Модель для вывода информации о расчете зарплаты."""
    id: int
    total_visits: int = Field(default=0, description="Общее количество визитов")
    completed_visits: int = Field(default=0, description="Завершенные визиты")
    cancelled_visits: int = Field(default=0, description="Отмененные визиты")
    total_revenue: float = Field(default=0.0, description="Общая сумма визитов")
    commission_amount: float = Field(default=0.0, description="Сумма комиссии")
    base_salary: float = Field(default=0.0, description="Фиксированная часть зарплаты")
    total_salary: float = Field(default=0.0, description="Итоговая зарплата")
    is_paid: bool = Field(default=False, description="Выплачена ли зарплата")
    paid_at: Optional[datetime] = Field(None, description="Дата выплаты")
    calculated_at: datetime
    calculated_by_id: Optional[int] = Field(None, description="ID пользователя, который рассчитал зарплату")
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SalaryCalculationWithEmployee(SalaryCalculationOut):
    """Модель расчета зарплаты с информацией о сотруднике."""
    employee: Optional[dict] = Field(None, description="Информация о сотруднике")


class CalculateSalaryRequest(BaseModel):
    """Запрос на расчет зарплаты."""
    period_start: datetime = Field(..., description="Начало периода расчета")
    period_end: datetime = Field(..., description="Конец периода расчета")
    notes: Optional[str] = Field(None, description="Заметки к расчету")

