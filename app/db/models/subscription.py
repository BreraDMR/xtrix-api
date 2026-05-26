from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship
from app.db.base import Base
class Subscription(Base):
    __tablename__ = "subscriptions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    plan_id = Column(Integer, ForeignKey("subscription_plans.id"), nullable=False)
    status = Column(String, default="active", index=True)  
    provider = Column(String, nullable=True)  
    external_id = Column(String, nullable=True)
    current_period_end = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    is_trial = Column(Boolean, default=False, nullable=False)  
    trial_start = Column(DateTime, nullable=True)  
    trial_end = Column(DateTime, nullable=True)  
    cancel_at_period_end = Column(Boolean, default=False, nullable=False)  
    cancelled_at = Column(DateTime, nullable=True, index=True)  
    reactivated_at = Column(DateTime, nullable=True)  
    last_transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)  
    user = relationship("User")
    plan = relationship("SubscriptionPlan")
    transactions = relationship("Transaction", back_populates="subscription", foreign_keys="Transaction.subscription_id", lazy="dynamic")
    last_transaction = relationship("Transaction", foreign_keys=[last_transaction_id], lazy="select", post_update=True)
