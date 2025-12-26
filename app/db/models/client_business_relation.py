from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, JSON, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from app.db.base import Base


class ClientBusinessRelation(Base):
    """
    Модель для связи клиента с бизнесом.
    Содержит бизнес-специфичные данные клиента (spending, personal_notes и т.д.)
    и обеспечивает изоляцию данных между бизнесами.
    """
    __tablename__ = "client_business_relations"

    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True)
    business_id = Column(Integer, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Бизнес-специфичные данные клиента (изолированные по бизнесам)
    spending = Column(Float, default=0.0, index=True)
    birthday_discount = Column(Boolean, default=False, index=True)
    personal_notes = Column(Text, nullable=True)  # Заметки специалиста о клиенте (изолированные)
    inventory = Column(JSON, nullable=True)  # Список купленных продуктов (изолированные)
    
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    
    # Уникальное ограничение: один клиент может быть связан с одним бизнесом только один раз
    __table_args__ = (
        UniqueConstraint('client_id', 'business_id', name='uq_client_business'),
    )
    
    # Relationships
    client = relationship("Client", back_populates="business_relations", lazy="select")
    business = relationship("Business", back_populates="client_relations", lazy="select")

