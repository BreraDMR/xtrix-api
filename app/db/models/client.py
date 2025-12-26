from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.db.base import Base


class Client(Base):
    __tablename__ = "clients"

    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)  # Made nullable for global clients
    full_name = Column(String, nullable=False, index=True)
    birth_date = Column(Date, nullable=True, index=True)
    gender = Column(String, nullable=True, index=True)
    contact_phone = Column(String, index=True, nullable=True)
    contact_email = Column(String, index=True, nullable=True)
    # Бизнес-специфичные данные перенесены в ClientBusinessRelation
    # Медицинские данные перенесены в MedicalProfile
    location_lat = Column(Float, nullable=True)
    location_lon = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    qr_code = Column(String, unique=True, index=True, nullable=True)
    access_password = Column(String, nullable=True)  # Hashed password for client access
    deleted_at = Column(DateTime, nullable=True, index=True)  # Soft delete
    archived_at = Column(DateTime, nullable=True, index=True)  # Archiving

    owner = relationship("User", foreign_keys=[owner_id], back_populates="clients", lazy="select")  # Deprecated - использовать business_relations
    visits = relationship("Visit", back_populates="client", lazy="dynamic", cascade="all,delete")
    specialist_relations = relationship("ClientSpecialistRelation", back_populates="client", lazy="dynamic", cascade="all,delete")
    business_relations = relationship("ClientBusinessRelation", back_populates="client", lazy="dynamic", cascade="all,delete")
    medical_profile = relationship("MedicalProfile", back_populates="client", uselist=False, cascade="all,delete")  # One-to-one relationship


