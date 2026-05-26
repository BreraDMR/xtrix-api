from sqlalchemy import Boolean, Column, Float, Integer, String, Interval
from app.db.base import Base
class SubscriptionPlan(Base):
    __tablename__ = "subscription_plans"
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    period_days = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)
    currency = Column(String, default="usd")
    features = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    is_trial = Column(Boolean, default=False, nullable=False)  
    trial_days = Column(Integer, nullable=True)  
