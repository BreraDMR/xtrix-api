from datetime import datetime
from typing import Optional
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from app.db.base import Base
class SpecialistProfile(Base):
    __tablename__ = "specialist_profiles"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True, index=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=True, index=True)  
    specialist_type = Column(String, nullable=True, index=True)  
    specialist_category = Column(String, nullable=True, index=True)  
    specialization = Column(Text, nullable=True)  
    experience_years = Column(Integer, nullable=True)  
    education = Column(Text, nullable=True)  
    certifications = Column(Text, nullable=True)  
    business_name = Column(String, nullable=True)  
    business_type = Column(String, nullable=True)  
    address = Column(Text, nullable=True)  
    description = Column(Text, nullable=True)  
    languages = Column(String, nullable=True)  
    is_verified = Column(String, default="pending", nullable=False)  
    verified_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    user = relationship("User", backref="specialist_profile", lazy="select")
    business = relationship("Business", back_populates="specialist_profiles", lazy="select")
