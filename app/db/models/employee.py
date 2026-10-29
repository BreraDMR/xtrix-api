from sqlalchemy import CheckConstraint, Numeric, UniqueConstraint, Column, DateTime, Integer, String, Boolean, ForeignKey, Float, JSON, Text
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.api.core.clock import utcnow
class Employee(Base):
    __tablename__ = "employees"
    __table_args__ = (CheckConstraint('(base_salary_currency IS NULL AND base_salary_exact IS NULL) OR (base_salary_currency IS NOT NULL AND base_salary_currency COLLATE "C" ~ \'^[A-Z]{3}$\'  AND base_salary_exact IS NOT NULL AND base_salary_exact BETWEEN 0 AND 9999999999999999.99)', name='ck_employee_salary_snapshot'),)
    base_salary_currency = Column(String(3), nullable=True)
    base_salary_exact = Column(Numeric(18, 2), nullable=True)
    id = Column(Integer, primary_key=True, index=True)
    business_owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)  
    employee_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)  
    position = Column(String, nullable=True)  
    permissions = Column(JSON, nullable=True)
    commission_percentage = Column(Float, nullable=True, default=0.0)  
    base_salary = Column(Float, nullable=True, default=0.0)  
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    hired_at = Column(DateTime, default=utcnow, nullable=False, index=True)
    fired_at = Column(DateTime, nullable=True, index=True)
    notes = Column(Text, nullable=True)  
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    business_owner = relationship("User", foreign_keys=[business_owner_id], lazy="select", backref="managed_employees")
    employee_user = relationship("User", foreign_keys=[employee_user_id], lazy="select", backref="employee_record")
