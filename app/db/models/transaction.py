from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.db.base import Base


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    subscription_id = Column(Integer, ForeignKey("subscriptions.id"), nullable=False, index=True)
    amount = Column(Float, nullable=False)  # Сумма транзакции
    currency = Column(String, default="eur", nullable=False)  # Валюта
    payment_method = Column(String, nullable=False)  # "card", "apple_pay", "google_pay", "erl_pay"
    status = Column(String, nullable=False, default="pending", index=True)  # "pending", "succeeded", "failed", "refunded", "cancelled"
    transaction_date = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    external_transaction_id = Column(String, nullable=True, index=True)  # ID в платежной системе
    description = Column(Text, nullable=True)  # Описание транзакции
    provider = Column(String, nullable=True)  # "stripe", "apple", "google", "erl"
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    subscription = relationship("Subscription", back_populates="transactions")

