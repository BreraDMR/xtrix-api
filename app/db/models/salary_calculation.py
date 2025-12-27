from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, Integer, Float, ForeignKey, Boolean, Text
from sqlalchemy.orm import relationship

from app.db.base import Base


class SalaryCalculation(Base):
    """Модель для расчета зарплат сотрудников."""
    __tablename__ = "salary_calculations"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)  # Связь с Employee
    
    # Период расчета
    period_start = Column(DateTime, nullable=False, index=True)
    period_end = Column(DateTime, nullable=False, index=True)
    
    # Статистика по визитам
    total_visits = Column(Integer, default=0, nullable=False)  # Общее количество визитов
    completed_visits = Column(Integer, default=0, nullable=False)  # Завершенные визиты
    cancelled_visits = Column(Integer, default=0, nullable=False)  # Отмененные визиты
    
    # Финансовые показатели
    total_revenue = Column(Float, default=0.0, nullable=False)  # Общая сумма визитов сотрудника
    commission_amount = Column(Float, default=0.0, nullable=False)  # Сумма комиссии (% от revenue)
    base_salary = Column(Float, default=0.0, nullable=False)  # Фиксированная часть зарплаты
    total_salary = Column(Float, default=0.0, nullable=False)  # Итоговая зарплата (base + commission)
    
    # Статус выплаты
    is_paid = Column(Boolean, default=False, nullable=False, index=True)  # Выплачена ли зарплата
    paid_at = Column(DateTime, nullable=True, index=True)  # Дата выплаты
    
    # Дополнительная информация
    notes = Column(Text, nullable=True)  # Заметки к расчету
    
    # Метаданные
    calculated_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    calculated_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # Кто рассчитал (admin/business_owner)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    employee = relationship("Employee", foreign_keys=[employee_id], lazy="select", backref="salary_calculations")
    calculated_by = relationship("User", foreign_keys=[calculated_by_id], lazy="select")

