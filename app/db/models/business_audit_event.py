"""Business-scoped journal of staff actions.

This is deliberately separate from the legacy platform ``audit_logs`` table: those rows
are admin-only and predate tenants, so they must never appear in a salon's journal.
"""
from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, JSON, String
from sqlalchemy.orm import relationship

from app.api.core.clock import utcnow
from app.db.base import Base


class BusinessAuditEvent(Base):
    __tablename__ = "business_audit_events"
    __table_args__ = (
        # A business event belongs to a business; a solo specialist's event has no business
        # and is scoped to that user. Exactly one scope is set.
        CheckConstraint(
            "(business_id IS NULL) <> (owner_user_id IS NULL)",
            name="ck_business_audit_events_scope",
        ),
        Index("ix_business_audit_events_business_created", "business_id", "created_at"),
        Index("ix_business_audit_events_owner_created", "owner_user_id", "created_at"),
        Index("ix_business_audit_events_business_action", "business_id", "action"),
        Index("ix_business_audit_events_business_entity", "business_id", "entity_type", "entity_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(
        Integer,
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # Solo specialist scope; never set together with business_id.
    owner_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    actor_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Snapshots keep the row readable after a staff member is renamed or removed.
    actor_name = Column(String(255), nullable=True)
    actor_role = Column(String(32), nullable=True)
    action = Column(String(64), nullable=False, index=True)
    entity_type = Column(String(32), nullable=False, index=True)
    entity_id = Column(Integer, nullable=False, index=True)
    request_id = Column(String(64), nullable=True, index=True)
    changes = Column(JSON(none_as_null=True), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow, index=True)

    business = relationship("Business", lazy="select")
    owner_user = relationship("User", foreign_keys=[owner_user_id], lazy="select")
    actor_user = relationship("User", foreign_keys=[actor_user_id], lazy="select")
