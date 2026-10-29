from sqlalchemy import CheckConstraint, Numeric, UniqueConstraint, Column, DateTime, Integer, Float, ForeignKey, String, Text
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.api.core.clock import utcnow
class SalaryPayment(Base):
    __tablename__ = "salary_payments"
    __table_args__ = (CheckConstraint('(currency IS NULL AND amount_exact IS NULL) OR (currency IS NOT NULL AND currency COLLATE "C" ~ \'^[A-Z]{3}$\'  AND amount_exact IS NOT NULL AND amount_exact BETWEEN 0 AND 9999999999999999.99)', name='ck_salary_payment_snapshot'),)
    currency = Column(String(3), nullable=True)
    amount_exact = Column(Numeric(18, 2), nullable=True)
    id = Column(Integer, primary_key=True, index=True)
    salary_calculation_id = Column(Integer, ForeignKey("salary_calculations.id", ondelete="CASCADE"), nullable=False, index=True)
    amount = Column(Float, nullable=False)
    payment_date = Column(DateTime, nullable=False, index=True)
    payment_method = Column(String, nullable=False, default="other")  
    status = Column(String, nullable=False, default="completed", index=True)  
    notes = Column(Text, nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    salary_calculation = relationship("SalaryCalculation", foreign_keys=[salary_calculation_id], backref="payments")
    created_by = relationship("User", foreign_keys=[created_by_id], lazy="select")
