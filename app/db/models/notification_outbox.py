"""Client notifications waiting to be delivered, one row per event key."""
from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, JSON, String, UniqueConstraint

from app.api.core.clock import utcnow
from app.db.base import Base


class NotificationOutbox(Base):
    __tablename__ = "notification_outbox"
    __table_args__ = (
        UniqueConstraint("event_key", name="uq_notification_outbox_event_key"),
        Index("ix_notification_outbox_status_scheduled_at", "status", "scheduled_at"),
    )

    id = Column(Integer, primary_key=True)
    # account-level messages (password reset) have no business and must not be gated by its settings
    business_id = Column(
        Integer, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=True, index=True
    )
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    visit_id = Column(Integer, ForeignKey("visits.id", ondelete="SET NULL"), nullable=True, index=True)
    event_key = Column(String(160), nullable=False)
    kind = Column(String(50), nullable=False)
    channel = Column(String(20), nullable=False, default="email", server_default="email")
    recipient = Column(String(320), nullable=False)
    language = Column(String(2), nullable=True)
    payload = Column(JSON, nullable=False, default=dict)
    status = Column(String(20), nullable=False, default="pending", server_default="pending")
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    scheduled_at = Column(DateTime, nullable=False, default=utcnow)
    locked_until = Column(DateTime, nullable=True)
    provider = Column(String(20), nullable=True)
    provider_message_id = Column(String(200), nullable=True)
    last_error = Column(String(500), nullable=True)
    sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)
