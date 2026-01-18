from datetime import datetime
from sqlalchemy import Column, DateTime, Integer, Float, ForeignKey, String, Text
from sqlalchemy.orm import relationship

from app.db.base import Base


class SalaryAdjustment(Base):
    """Модель для дополнительных начислений/удержаний к зарплате."""
    __tablename__ = "salary_adjustments"

    id = Column(Integer, primary_key=True, index=True)
    salary_calculation_id = Column(Integer, ForeignKey("salary_calculations.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Тип начисления
    type = Column(String, nullable=False, index=True)  # 'bonus', 'penalty', 'overtime', 'allowance', 'deduction'
    
    # Сумма (может быть отрицательной для штрафов/удержаний)
    amount = Column(Float, nullable=False)
    
    # Описание
    description = Column(Text, nullable=True)
    
    # Кто создал
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    # Временные метки
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    salary_calculation = relationship("SalaryCalculation", foreign_keys=[salary_calculation_id], backref="adjustments")
    created_by = relationship("User", foreign_keys=[created_by_id], lazy="select")
