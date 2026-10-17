from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.api.core.clock import utcnow
class Client(Base):
    __tablename__ = "clients"
    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)  
    full_name = Column(String, nullable=False, index=True)
    birth_date = Column(Date, nullable=True, index=True)
    gender = Column(String, nullable=True, index=True)
    contact_phone = Column(String, index=True, nullable=True)
    contact_email = Column(String, index=True, nullable=True)
    location_lat = Column(Float, nullable=True)
    location_lon = Column(Float, nullable=True)
    # language of reminders/greetings sent to this client, independent of the staff UI language
    language = Column(String(2), nullable=False, default="ru", server_default="ru")
    created_at = Column(DateTime, default=utcnow, index=True)
    qr_code = Column(String, unique=True, index=True, nullable=True)
    access_password = Column(String, nullable=True)  
    qr_failed_attempts = Column(Integer, nullable=False, default=0, server_default="0")
    qr_locked_until = Column(DateTime, nullable=True)
    deleted_at = Column(DateTime, nullable=True, index=True)  
    archived_at = Column(DateTime, nullable=True, index=True)  
    owner = relationship("User", foreign_keys=[owner_id], back_populates="clients", lazy="select")  
    visits = relationship("Visit", back_populates="client", lazy="dynamic", cascade="all,delete")
    specialist_relations = relationship("ClientSpecialistRelation", back_populates="client", lazy="dynamic", cascade="all,delete")
    business_relations = relationship("ClientBusinessRelation", back_populates="client", lazy="dynamic", cascade="all,delete")
    medical_profile = relationship("MedicalProfile", back_populates="client", uselist=False, cascade="all,delete")  
