"""One submitted answer set per visit, kept against the exact published version."""
from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint

from app.api.core.clock import utcnow
from app.db.base import Base


class QuestionnaireResponse(Base):
    __tablename__ = "questionnaire_responses"
    __table_args__ = (
        UniqueConstraint("visit_id", name="uq_questionnaire_response_visit"),
    )

    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(Integer, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True)
    visit_id = Column(Integer, ForeignKey("visits.id", ondelete="CASCADE"), nullable=False)
    version_id = Column(Integer, ForeignKey("questionnaire_versions.id", ondelete="CASCADE"), nullable=False)
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="SET NULL"), nullable=True, index=True)
    access_token_id = Column(
        Integer,
        ForeignKey("questionnaire_access_tokens.id", ondelete="SET NULL"),
        nullable=True,
    )
    answers = Column(JSON(none_as_null=True), nullable=False)
    request_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)
