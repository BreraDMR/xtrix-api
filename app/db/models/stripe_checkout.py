from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String

from app.db.base import Base


class StripeCheckoutState(Base):
    __tablename__ = "stripe_checkout_state"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    customer_id = Column(String, unique=True, nullable=True)
    request_key = Column(String, nullable=True)
    request_payload = Column(JSON, nullable=True)
    session_id = Column(String, unique=True, nullable=True)
    attempt_started_at = Column(DateTime, nullable=True)
