from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, Integer, String, Boolean, ForeignKey, Float, JSON, Text
from sqlalchemy.orm import relationship

from app.db.base import Base


class Employee(Base):
    """Модель для управления сотрудниками бизнеса."""
    __tablename__ = "employees"

    id = Column(Integer, primary_key=True, index=True)
    business_owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)  # Владелец бизнеса
    employee_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)  # Сотрудник (User с role=employee или specialist)
    position = Column(String, nullable=True)  # Должность: "массажист", "администратор", "косметолог" и т.д.
    
    # Права доступа (JSON)
    # {
    #   "can_create_visits": true,
    #   "can_edit_clients": true,
    #   "can_view_financials": false,
    #   "can_manage_services": true,
    #   "assigned_client_ids": [1, 2, 3]  # null или [] = все клиенты бизнеса
    # }
    permissions = Column(JSON, nullable=True)
    
    # Зарплата
    commission_percentage = Column(Float, nullable=True, default=0.0)  # % комиссии с каждого визита
    base_salary = Column(Float, nullable=True, default=0.0)  # Фиксированная часть зарплаты
    
    # Статус
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    hired_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    fired_at = Column(DateTime, nullable=True, index=True)
    
    # Дополнительная информация
    notes = Column(Text, nullable=True)  # Заметки о сотруднике
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    business_owner = relationship("User", foreign_keys=[business_owner_id], lazy="select", backref="managed_employees")
    employee_user = relationship("User", foreign_keys=[employee_user_id], lazy="select", backref="employee_record")

