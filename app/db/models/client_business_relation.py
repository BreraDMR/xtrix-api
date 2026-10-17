from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, JSON, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.api.core.clock import utcnow
class ClientBusinessRelation(Base):
    __tablename__ = "client_business_relations"
    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True)
    business_id = Column(Integer, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True)
    birthday_discount = Column(Boolean, default=False, index=True)
    personal_notes = Column(Text, nullable=True)  
    inventory = Column(JSON, nullable=True)  
    loyalty_points = Column(Float, default=0.0, nullable=False, index=True)  
    loyalty_cashback = Column(Float, default=0.0, nullable=False, index=True)  
    loyalty_level_id = Column(Integer, ForeignKey("loyalty_levels.id", ondelete="SET NULL"), nullable=True, index=True)  
    created_at = Column(DateTime, default=utcnow, index=True)
    __table_args__ = (
        UniqueConstraint('client_id', 'business_id', name='uq_client_business'),
    )
    client = relationship("Client", back_populates="business_relations", lazy="select")
    business = relationship("Business", back_populates="client_relations", lazy="select")
