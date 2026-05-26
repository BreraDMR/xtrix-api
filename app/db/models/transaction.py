from datetime import datetime
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from app.db.base import Base
class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, index=True)
    subscription_id = Column(Integer, ForeignKey("subscriptions.id"), nullable=False, index=True)
    amount = Column(Float, nullable=False)  
    currency = Column(String, default="eur", nullable=False)  
    payment_method = Column(String, nullable=False)  
    status = Column(String, nullable=False, default="pending", index=True)  
    transaction_date = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    external_transaction_id = Column(String, nullable=True, index=True)  
    description = Column(Text, nullable=True)  
    provider = Column(String, nullable=True)  
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    subscription = relationship("Subscription", back_populates="transactions", foreign_keys=[subscription_id])
