"""Questionnaire drafts and the frozen versions guests actually answer."""
from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import relationship

from app.api.core.clock import utcnow
from app.db.base import Base


class QuestionnaireTemplate(Base):
    """An editable draft of one business, optionally bound to one service.

    Publishing copies this draft into QuestionnaireVersion, so later edits never
    change the wording behind an answer that has already been given.
    """
    __tablename__ = "questionnaire_templates"
    __table_args__ = (
        UniqueConstraint("service_id", name="uq_questionnaire_template_service"),
    )

    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(Integer, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True)
    service_id = Column(Integer, ForeignKey("services.id", ondelete="SET NULL"), nullable=True)
    name = Column(String(255), nullable=False)
    description = Column(String(500), nullable=True)
    draft_fields = Column(JSON(none_as_null=True), nullable=False, default=list)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    versions = relationship(
        "QuestionnaireVersion",
        back_populates="template",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="QuestionnaireVersion.version",
    )


class QuestionnaireVersion(Base):
    """A published snapshot; created once and never updated afterwards."""
    __tablename__ = "questionnaire_versions"
    __table_args__ = (
        UniqueConstraint("template_id", "version", name="uq_questionnaire_version_number"),
        CheckConstraint("version >= 1", name="ck_questionnaire_version_number"),
    )

    id = Column(Integer, primary_key=True, index=True)
    template_id = Column(Integer, ForeignKey("questionnaire_templates.id", ondelete="CASCADE"), nullable=False)
    version = Column(Integer, nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(String(500), nullable=True)
    fields = Column(JSON(none_as_null=True), nullable=False)
    published_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    published_at = Column(DateTime, nullable=False, default=utcnow)

    template = relationship("QuestionnaireTemplate", back_populates="versions")
    published_by = relationship("User")
