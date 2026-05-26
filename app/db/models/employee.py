from datetime import datetime
from typing import Optional
from sqlalchemy import Column, DateTime, Integer, String, Boolean, ForeignKey, Float, JSON, Text
from sqlalchemy.orm import relationship
from app.db.base import Base
class Employee(Base):
    __tablename__ = "employees"
    id = Column(Integer, primary_key=True, index=True)
    business_owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)  
    employee_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)  
    position = Column(String, nullable=True)  
    permissions = Column(JSON, nullable=True)
    commission_percentage = Column(Float, nullable=True, default=0.0)  
    base_salary = Column(Float, nullable=True, default=0.0)  
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    hired_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    fired_at = Column(DateTime, nullable=True, index=True)
    notes = Column(Text, nullable=True)  
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    business_owner = relationship("User", foreign_keys=[business_owner_id], lazy="select", backref="managed_employees")
    employee_user = relationship("User", foreign_keys=[employee_user_id], lazy="select", backref="employee_record")
