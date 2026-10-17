from datetime import datetime
from sqlalchemy import Column, DateTime, Integer, Float, ForeignKey, Boolean, Text, String
from sqlalchemy.orm import relationship
from app.db.base import Base
class SalaryCalculation(Base):
    __tablename__ = "salary_calculations"
    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)  
    period_start = Column(DateTime, nullable=False, index=True)
    period_end = Column(DateTime, nullable=False, index=True)
    total_visits = Column(Integer, default=0, nullable=False)  
    completed_visits = Column(Integer, default=0, nullable=False)  
    cancelled_visits = Column(Integer, default=0, nullable=False)  
    total_revenue = Column(Float, default=0.0, nullable=False)  
    commission_amount = Column(Float, default=0.0, nullable=False)  
    base_salary = Column(Float, default=0.0, nullable=False)  
    total_salary = Column(Float, default=0.0, nullable=False)  
    adjustments_total = Column(Float, default=0.0, nullable=False)  
    total_with_adjustments = Column(Float, default=0.0, nullable=False)  
    paid_amount = Column(Float, default=0.0, nullable=False)  
    payment_status = Column(String, default="unpaid", nullable=False, index=True)  
    is_paid = Column(Boolean, default=False, nullable=False, index=True)  
    paid_at = Column(DateTime, nullable=True, index=True)  
    notes = Column(Text, nullable=True)  
    calculated_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    calculated_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)  
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    employee = relationship("Employee", foreign_keys=[employee_id], lazy="select", backref="salary_calculations")
    calculated_by = relationship("User", foreign_keys=[calculated_by_id], lazy="select")
