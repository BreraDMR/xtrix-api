from sqlalchemy import Boolean, Column, DateTime, Float, Index, Integer, String

from app.api.core.clock import utcnow
from app.db.base import Base


class AIUsage(Base):
    """One row per assistant model request: who, which slot and model, how many tokens.
    Never the key, never the text. A plain log, so no foreign keys (see the migration)."""
    __tablename__ = "ai_usage"
    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(Integer, nullable=True)
    user_id = Column(Integer, nullable=True)
    provider = Column(String(64), nullable=False)
    slot_index = Column(Integer, nullable=True)
    model = Column(String(128), nullable=False)
    role = Column(String(16), nullable=False, default="fast")
    prompt_tokens = Column(Integer, nullable=False, default=0)
    completion_tokens = Column(Integer, nullable=False, default=0)
    cost_estimate = Column(Float, nullable=False, default=0.0)
    succeeded = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (
        Index("ix_ai_usage_business_created", "business_id", "created_at"),
        Index("ix_ai_usage_user_created", "user_id", "created_at"),
    )
