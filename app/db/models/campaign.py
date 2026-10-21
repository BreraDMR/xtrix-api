"""A marketing campaign: owner-written text, a frozen audience and an optional schedule.

Drafts can change freely; confirmation snapshots the text and the recipients so the messages
the owner approved are exactly the messages the outbox later queues.
"""
from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import relationship

from app.api.core.clock import utcnow
from app.db.base import Base

CAMPAIGN_STATUSES = ("draft", "confirmed", "sending", "sent", "cancelled")


class Campaign(Base):
    __tablename__ = "campaigns"
    __table_args__ = (
        Index("ix_campaigns_business_status", "business_id", "status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(
        Integer,
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(120), nullable=False)
    subject = Column(String(200), nullable=False)
    body = Column(Text, nullable=False)
    segment_kind = Column(
        String(32), nullable=False, default="no_visits", server_default="no_visits"
    )
    inactive_days = Column(Integer, nullable=False, default=90, server_default="90")
    scheduled_at = Column(DateTime, nullable=True)
    status = Column(
        String(16), nullable=False, default="draft", server_default="draft", index=True
    )
    audience_snapshot = Column(JSON, nullable=True)
    audience_hash = Column(String(64), nullable=True)
    content_hash = Column(String(64), nullable=True)
    recipient_count = Column(Integer, nullable=False, default=0, server_default="0")
    queued_count = Column(Integer, nullable=False, default=0, server_default="0")
    confirmed_at = Column(DateTime, nullable=True)
    confirmed_by = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime, nullable=False, default=utcnow, index=True)
    updated_at = Column(
        DateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    business = relationship("Business", lazy="select")
    confirmed_by_user = relationship("User", foreign_keys=[confirmed_by], lazy="select")
    created_by_user = relationship("User", foreign_keys=[created_by], lazy="select")
