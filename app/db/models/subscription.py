from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.db.base import Base


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    plan_id = Column(Integer, ForeignKey("subscription_plans.id"), nullable=False)
    status = Column(String, default="active", index=True)  # "active", "cancelled", "expired", "pending_cancellation"
    provider = Column(String, nullable=True)  # stripe | apple | google | erl
    external_id = Column(String, nullable=True)
    current_period_end = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    # Trial fields
    is_trial = Column(Boolean, default=False, nullable=False)  # Активен ли trial
    trial_start = Column(DateTime, nullable=True)  # Начало trial периода
    trial_end = Column(DateTime, nullable=True)  # Конец trial периода
    # Cancellation fields
    cancel_at_period_end = Column(Boolean, default=False, nullable=False)  # Отменена, но активна до конца периода
    cancelled_at = Column(DateTime, nullable=True, index=True)  # Когда была отменена
    reactivated_at = Column(DateTime, nullable=True)  # Когда была возобновлена
    last_transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)  # Последняя транзакция

    user = relationship("User")
    plan = relationship("SubscriptionPlan")
    transactions = relationship("Transaction", back_populates="subscription", foreign_keys="Transaction.subscription_id", lazy="dynamic")
    last_transaction = relationship("Transaction", foreign_keys=[last_transaction_id], lazy="select", post_update=True)











