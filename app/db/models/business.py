from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.api.core.clock import utcnow
class Business(Base):
    __tablename__ = "businesses"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, index=True)
    business_type = Column(
        String, 
        nullable=False, 
        index=True,
        comment="Business type: salon, clinic, medical, dental, hospital, individual"
    )
    address = Column(Text, nullable=True)
    phone = Column(String, nullable=True, index=True)
    email = Column(String, nullable=True, index=True)
    description = Column(Text, nullable=True)
    currency = Column(String, nullable=False, default="EUR", index=True)  
    created_at = Column(DateTime, default=utcnow, index=True)
    deleted_at = Column(DateTime, nullable=True, index=True)  
    users = relationship("User", back_populates="business", lazy="dynamic")
    specialist_profiles = relationship("SpecialistProfile", back_populates="business", lazy="dynamic")
    client_relations = relationship("ClientBusinessRelation", back_populates="business", lazy="dynamic", cascade="all,delete")
    visits = relationship("Visit", back_populates="business", lazy="dynamic")
    services = relationship("Service", back_populates="business", lazy="dynamic")
