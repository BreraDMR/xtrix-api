from datetime import datetime
from sqlalchemy import Column, DateTime, Integer, ForeignKey
from sqlalchemy.orm import relationship
from app.db.base import Base
class ServiceSpecialist(Base):
    __tablename__ = "service_specialists"
    id = Column(Integer, primary_key=True, index=True)
    service_id = Column(Integer, ForeignKey("services.id", ondelete="CASCADE"), nullable=False, index=True)
    specialist_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=True)
    service = relationship("Service", back_populates="service_specialists", lazy="select")
    specialist = relationship("User", lazy="select")
    __table_args__ = (
        {"comment": "Many-to-many relationship between services and specialists"}
    )
