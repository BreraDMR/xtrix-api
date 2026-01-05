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
    # Trial fields
    is_trial = Column(Boolean, default=False, nullable=False)  # Является ли план trial
    trial_days = Column(Integer, nullable=True)  # Количество дней trial (7-14 для solo, 3-5 для admin)











