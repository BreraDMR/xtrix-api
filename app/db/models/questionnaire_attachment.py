"""Private files attached to one visit's questionnaire; the object key is server-made."""
from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint

from app.api.core.clock import utcnow
from app.db.base import Base


class QuestionnaireAttachment(Base):
    __tablename__ = "questionnaire_attachments"
    __table_args__ = (
        UniqueConstraint("storage_key", name="uq_questionnaire_attachment_key"),
        CheckConstraint(
            "size_bytes > 0 AND size_bytes <= 10485760",
            name="ck_questionnaire_attachment_size",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(Integer, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    visit_id = Column(Integer, ForeignKey("visits.id", ondelete="CASCADE"), nullable=False, index=True)
    version_id = Column(Integer, ForeignKey("questionnaire_versions.id", ondelete="CASCADE"), nullable=False)
    token_id = Column(
        Integer,
        ForeignKey("questionnaire_access_tokens.id", ondelete="SET NULL"),
        nullable=True,
    )
    storage_key = Column(String(255), nullable=False)
    original_filename = Column(String(255), nullable=False)
    content_type = Column(String(100), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    uploaded_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
