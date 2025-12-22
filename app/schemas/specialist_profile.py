"""Pydantic schemas for specialist profiles."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class SpecialistProfileBase(BaseModel):
    """Base schema for specialist profile."""
    specialist_type: Optional[str] = Field(None, description="Type of specialist (e.g., 'massage', 'cosmetologist', 'dentist')")
    specialist_category: Optional[str] = Field(None, description="Category: 'beauty', 'wellness', or 'healthcare'")
    specialization: Optional[str] = Field(None, description="Detailed specialization description")
    experience_years: Optional[int] = Field(None, ge=0, le=100, description="Years of experience")
    education: Optional[str] = None
    certifications: Optional[str] = None
    business_name: Optional[str] = Field(None, max_length=255)
    business_type: Optional[str] = Field(None, description="'individual', 'salon', 'clinic', 'hospital'")
    address: Optional[str] = None
    description: Optional[str] = None
    languages: Optional[str] = Field(None, description="Comma-separated list of languages")
    
    @field_validator('business_type')
    @classmethod
    def validate_business_type(cls, v):
        if v is not None and v not in ['individual', 'salon', 'clinic', 'hospital', 'barbershop', 'rehab_center']:
            raise ValueError("business_type must be one of: individual, salon, clinic, hospital, barbershop, rehab_center")
        return v
    
    @field_validator('specialist_category')
    @classmethod
    def validate_category(cls, v):
        if v is not None and v not in ['beauty', 'wellness', 'healthcare']:
            raise ValueError("specialist_category must be one of: beauty, wellness, healthcare")
        return v


class SpecialistProfileCreate(SpecialistProfileBase):
    """Schema for creating a specialist profile."""
    user_id: int


class SpecialistProfileUpdate(BaseModel):
    """Schema for updating a specialist profile (all fields optional)."""
    specialist_type: Optional[str] = None
    specialist_category: Optional[str] = None
    specialization: Optional[str] = None
    experience_years: Optional[int] = Field(None, ge=0, le=100)
    education: Optional[str] = None
    certifications: Optional[str] = None
    business_name: Optional[str] = Field(None, max_length=255)
    business_type: Optional[str] = None
    address: Optional[str] = None
    description: Optional[str] = None
    languages: Optional[str] = None
    is_verified: Optional[str] = Field(None, description="'pending', 'verified', 'rejected'")
    
    @field_validator('business_type')
    @classmethod
    def validate_business_type(cls, v):
        if v is not None and v not in ['individual', 'salon', 'clinic', 'hospital', 'barbershop', 'rehab_center']:
            raise ValueError("business_type must be one of: individual, salon, clinic, hospital, barbershop, rehab_center")
        return v
    
    @field_validator('specialist_category')
    @classmethod
    def validate_category(cls, v):
        if v is not None and v not in ['beauty', 'wellness', 'healthcare']:
            raise ValueError("specialist_category must be one of: beauty, wellness, healthcare")
        return v
    
    @field_validator('is_verified')
    @classmethod
    def validate_verified_status(cls, v):
        if v is not None and v not in ['pending', 'verified', 'rejected']:
            raise ValueError("is_verified must be one of: pending, verified, rejected")
        return v


class SpecialistProfileOut(SpecialistProfileBase):
    """Schema for returning specialist profile."""
    id: int
    user_id: int
    is_verified: str
    verified_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True



