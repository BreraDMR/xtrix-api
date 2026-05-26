from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from sqlalchemy import Column, DateTime, Integer, String, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from app.db.base import Base
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String, nullable=False, index=True)
    phone = Column(String, unique=True, index=True, nullable=True)
    email = Column(String, unique=True, index=True, nullable=True)
    hashed_password = Column(String, nullable=False)
    role = Column(String, nullable=False, default="specialist", index=True)
    specialist_type = Column(String, nullable=True, index=True)  
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=True, index=True)  
    employer_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)  
    hire_date = Column(DateTime, nullable=True, index=True)  
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    failed_login_attempts = Column(Integer, default=0, nullable=False)
    locked_until = Column(DateTime, nullable=True, index=True)
    is_locked = Column(Boolean, default=False, nullable=False, index=True)
    deleted_at = Column(DateTime, nullable=True, index=True)  
    archived_at = Column(DateTime, nullable=True, index=True)  
    totp_secret = Column(String, nullable=True)  
    is_2fa_enabled = Column(Boolean, default=False, nullable=False)  
    moderation_status = Column(String, default="pending", nullable=False, index=True)  
    moderation_notes = Column(String, nullable=True)  
    moderated_at = Column(DateTime, nullable=True, index=True)  
    moderated_by_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)  
    clients = relationship("Client", foreign_keys="[Client.owner_id]", back_populates="owner", lazy="dynamic", cascade="all,delete")
    services = relationship("Service", back_populates="specialist", lazy="dynamic", cascade="all,delete")
    visits = relationship("Visit", back_populates="specialist", lazy="dynamic")
    refresh_tokens = relationship("RefreshToken", back_populates="user", lazy="dynamic", cascade="all,delete")
    client_relations = relationship("ClientSpecialistRelation", back_populates="specialist", lazy="dynamic", cascade="all,delete")
    business = relationship("Business", back_populates="users", lazy="select")
    employer = relationship("User", remote_side=[id], foreign_keys=[employer_id], lazy="select")  
    employees = relationship("User", foreign_keys=[employer_id], lazy="dynamic", overlaps="employer")  
    preferences = relationship("UserPreferences", back_populates="user", uselist=False, cascade="all,delete-orphan")
