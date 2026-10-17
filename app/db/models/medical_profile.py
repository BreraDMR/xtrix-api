from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, Text
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.api.core.clock import utcnow
class MedicalProfile(Base):
    __tablename__ = "medical_profiles"
    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    allergies = Column(Text, nullable=True)  
    chronic_diseases = Column(Text, nullable=True)  
    pregnancy = Column(Boolean, default=False, index=True)  
    viral_diseases = Column(Text, nullable=True)  
    ontology = Column(JSON, nullable=True)  
    blood_type = Column(Text, nullable=True)  
    medications = Column(JSON, nullable=True)  
    medical_history = Column(Text, nullable=True)  
    emergency_contact = Column(JSON, nullable=True)  
    insurance_info = Column(JSON, nullable=True)  
    doctor_notes = Column(Text, nullable=True)  
    created_at = Column(DateTime, default=utcnow, index=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, index=True)
    deleted_at = Column(DateTime, nullable=True, index=True)  
    client = relationship("Client", back_populates="medical_profile", lazy="select")
