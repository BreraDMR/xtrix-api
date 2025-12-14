from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.db.base import Base


class Client(Base):
    __tablename__ = "clients"

    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    full_name = Column(String, nullable=False, index=True)
    birth_date = Column(Date, nullable=True, index=True)
    gender = Column(String, nullable=True, index=True)
    allergies = Column(Text, nullable=True)
    chronic_diseases = Column(Text, nullable=True)
    pregnancy = Column(Boolean, default=False, index=True)
    ontology = Column(JSON, nullable=True)
    contact_phone = Column(String, index=True, nullable=True)
    contact_email = Column(String, index=True, nullable=True)
    viral_diseases = Column(Text, nullable=True)
    inventory = Column(JSON, nullable=True)  # список купленных продуктов
    spending = Column(Float, default=0.0, index=True)
    birthday_discount = Column(Boolean, default=False, index=True)
    personal_notes = Column(Text, nullable=True)
    location_lat = Column(Float, nullable=True)
    location_lon = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    qr_code = Column(String, unique=True, index=True, nullable=True)
    access_password = Column(String, nullable=True)  # Hashed password for client access
    deleted_at = Column(DateTime, nullable=True, index=True)  # Soft delete
    archived_at = Column(DateTime, nullable=True, index=True)  # Archiving

    owner = relationship("User", back_populates="clients", lazy="select")
    visits = relationship("Visit", back_populates="client", lazy="dynamic", cascade="all,delete")


