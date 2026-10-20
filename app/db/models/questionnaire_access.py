"""Hashed bearer tokens that let one visit's guest fill one questionnaire version."""
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint

from app.api.core.clock import utcnow
from app.db.base import Base


class QuestionnaireAccessToken(Base):
    __tablename__ = "questionnaire_access_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_questionnaire_access_token_hash"),
    )

    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(Integer, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True)
    visit_id = Column(Integer, ForeignKey("visits.id", ondelete="CASCADE"), nullable=False, index=True)
    version_id = Column(Integer, ForeignKey("questionnaire_versions.id", ondelete="CASCADE"), nullable=False)
    created_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    token_hash = Column(String(64), nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
