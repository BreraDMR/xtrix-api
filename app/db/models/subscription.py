from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.db.base import Base


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    plan_id = Column(Integer, ForeignKey("subscription_plans.id"), nullable=False)
    status = Column(String, default="active")
    provider = Column(String, nullable=True)  # stripe | apple | google
    external_id = Column(String, nullable=True)
    current_period_end = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    # Trial fields
    is_trial = Column(Boolean, default=False, nullable=False)  # Активен ли trial
    trial_start = Column(DateTime, nullable=True)  # Начало trial периода
    trial_end = Column(DateTime, nullable=True)  # Конец trial периода

    user = relationship("User")
    plan = relationship("SubscriptionPlan")











