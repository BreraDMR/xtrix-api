"""Specialist profile model with extended information."""
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.db.base import Base


class SpecialistProfile(Base):
    """
    Extended profile information for specialists.
    Contains specialist type, category, specialization, and other details.
    """
    __tablename__ = "specialist_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True, index=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=True, index=True)  # Связь с бизнесом (для обратной совместимости)
    
    # Specialist type and category
    specialist_type = Column(String, nullable=True, index=True)  # e.g., "massage", "cosmetologist", "dentist"
    specialist_category = Column(String, nullable=True, index=True)  # "beauty", "wellness", "healthcare"
    
    # Specialization details
    specialization = Column(Text, nullable=True)  # Detailed specialization description
    experience_years = Column(Integer, nullable=True)  # Years of experience
    education = Column(Text, nullable=True)  # Education background
    certifications = Column(Text, nullable=True)  # Certifications and licenses
    
    # Business information (deprecated - использовать Business модель)
    business_name = Column(String, nullable=True)  # Name of salon/clinic/business (deprecated)
    business_type = Column(String, nullable=True)  # "individual", "salon", "clinic", "hospital" (deprecated)
    address = Column(Text, nullable=True)  # Business address (deprecated)
    description = Column(Text, nullable=True)  # Public description
    
    # Additional metadata
    languages = Column(String, nullable=True)  # Comma-separated list of languages
    is_verified = Column(String, default="pending", nullable=False)  # "pending", "verified", "rejected"
    verified_at = Column(DateTime, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", backref="specialist_profile", lazy="select")
    business = relationship("Business", back_populates="specialist_profiles", lazy="select")





