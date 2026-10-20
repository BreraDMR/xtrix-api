from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.api.core.clock import utcnow
class Service(Base):
    __tablename__ = "services"
    __table_args__ = (
        CheckConstraint(
            "buffer_before_minutes >= 0 AND buffer_after_minutes >= 0",
            name="ck_service_buffers_non_negative",
        ),
    )
    id = Column(Integer, primary_key=True, index=True)
    specialist_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)  
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=True, index=True)  
    name = Column(String, nullable=False, index=True)
    category = Column(String, nullable=True, index=True)  
    description = Column(Text, nullable=True)
    duration_minutes = Column(Integer, nullable=True)
    # time around the visit reserved on the calendar, 0 keeps legacy services as they were
    buffer_before_minutes = Column(Integer, nullable=False, default=0, server_default="0")
    buffer_after_minutes = Column(Integer, nullable=False, default=0, server_default="0")
    price = Column(Float, nullable=True, index=True)
    is_active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=utcnow, index=True)
    deleted_at = Column(DateTime, nullable=True, index=True)  
    archived_at = Column(DateTime, nullable=True, index=True)  
    specialist = relationship("User", foreign_keys=[specialist_id], back_populates="services", lazy="select")
    business = relationship("Business", lazy="select")
    visits = relationship("Visit", back_populates="service", lazy="dynamic")
    service_specialists = relationship("ServiceSpecialist", back_populates="service", lazy="select", cascade="all, delete-orphan")
