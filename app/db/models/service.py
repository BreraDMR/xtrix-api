from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.db.base import Base


class Service(Base):
    __tablename__ = "services"

    id = Column(Integer, primary_key=True, index=True)
    specialist_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)  # Основной специалист (для обратной совместимости)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=True, index=True)  # Для изоляции данных по бизнесам (nullable для обратной совместимости)
    name = Column(String, nullable=False, index=True)
    category = Column(String, nullable=True, index=True)  # Категория услуги: beauty, wellness, healthcare (опционально, может определяться автоматически из типа специалиста)
    duration_minutes = Column(Integer, nullable=True)
    price = Column(Float, nullable=True, index=True)
    is_active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    deleted_at = Column(DateTime, nullable=True, index=True)  # Soft delete
    archived_at = Column(DateTime, nullable=True, index=True)  # Archiving

    specialist = relationship("User", foreign_keys=[specialist_id], back_populates="services", lazy="select")
    business = relationship("Business", lazy="select")
    visits = relationship("Visit", back_populates="service", lazy="dynamic")
    service_specialists = relationship("ServiceSpecialist", back_populates="service", lazy="select", cascade="all, delete-orphan")



