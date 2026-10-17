from sqlalchemy import Column, DateTime, Integer, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.api.core.clock import utcnow
class ClientSpecialistRelation(Base):
    __tablename__ = "client_specialist_relations"
    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True)
    specialist_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    business_id = Column(Integer, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True)  
    created_at = Column(DateTime, default=utcnow, index=True)
    __table_args__ = (
        UniqueConstraint('client_id', 'specialist_id', 'business_id', name='uq_client_specialist_business'),
    )
    client = relationship("Client", back_populates="specialist_relations", lazy="select")
    specialist = relationship("User", back_populates="client_relations", lazy="select")
    business = relationship("Business", lazy="select")
