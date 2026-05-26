from datetime import datetime
from sqlalchemy import Column, DateTime, Integer, Float, ForeignKey, String, Text
from sqlalchemy.orm import relationship
from app.db.base import Base
class SalaryPayment(Base):
    __tablename__ = "salary_payments"
    id = Column(Integer, primary_key=True, index=True)
    salary_calculation_id = Column(Integer, ForeignKey("salary_calculations.id", ondelete="CASCADE"), nullable=False, index=True)
    amount = Column(Float, nullable=False)
    payment_date = Column(DateTime, nullable=False, index=True)
    payment_method = Column(String, nullable=False, default="other")  
    status = Column(String, nullable=False, default="completed", index=True)  
    notes = Column(Text, nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    salary_calculation = relationship("SalaryCalculation", foreign_keys=[salary_calculation_id], backref="payments")
    created_by = relationship("User", foreign_keys=[created_by_id], lazy="select")
