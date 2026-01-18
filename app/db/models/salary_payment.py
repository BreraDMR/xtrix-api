from datetime import datetime
from sqlalchemy import Column, DateTime, Integer, Float, ForeignKey, String, Text
from sqlalchemy.orm import relationship

from app.db.base import Base


class SalaryPayment(Base):
    """Модель для истории выплат зарплат."""
    __tablename__ = "salary_payments"

    id = Column(Integer, primary_key=True, index=True)
    salary_calculation_id = Column(Integer, ForeignKey("salary_calculations.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Сумма выплаты (может быть частичной)
    amount = Column(Float, nullable=False)
    
    # Дата выплаты
    payment_date = Column(DateTime, nullable=False, index=True)
    
    # Способ оплаты
    payment_method = Column(String, nullable=False, default="other")  # 'cash', 'card', 'transfer', 'other'
    
    # Статус выплаты
    status = Column(String, nullable=False, default="completed", index=True)  # 'completed', 'pending', 'failed'
    
    # Заметки
    notes = Column(Text, nullable=True)
    
    # Кто создал/зарегистрировал выплату
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    # Временные метки
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    salary_calculation = relationship("SalaryCalculation", foreign_keys=[salary_calculation_id], backref="payments")
    created_by = relationship("User", foreign_keys=[created_by_id], lazy="select")
