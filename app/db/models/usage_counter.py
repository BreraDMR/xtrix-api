from decimal import Decimal
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)

from app.api.core.clock import utcnow
from app.db.base import Base


class UsageCounter(Base):
    """Stores product usage counters per billing period.

    Tracks active clients count for CRM and request units for AI.
    Monetary amounts strictly use Decimal, not float.
    """
    __tablename__ = "usage_counters"
    __table_args__ = (
        UniqueConstraint("business_id", "product", "period", "unit", name="uq_usage_counter_scope"),
        Index("ix_usage_counter_lookup", "business_id", "product", "period"),
    )

    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(Integer, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    product = Column(String(32), nullable=False)  # "crm", "ai"
    period = Column(String(32), nullable=False)   # e.g. "2026-10" or "2026-10-20"
    unit = Column(String(32), nullable=False)     # "active_clients", "ai_requests"
    count = Column(Integer, nullable=False, default=0)
    money_amount = Column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    policy_version = Column(String(32), nullable=False, default="2026-10-v1")
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)
