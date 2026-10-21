"""Waitlist requests and time-limited offers for freed slots.

An entry is a client's preference, not a reservation. An offer only points at a slot that
was free at the moment it was queued; acceptance checks availability again, so two offers
never turn into two visits for one time. Only token digests are stored.
"""
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)

from app.api.core.clock import utcnow
from app.db.base import Base


class WaitlistEntry(Base):
    __tablename__ = "waitlist_entries"
    __table_args__ = (
        Index(
            "uq_waitlist_entries_active_dedupe",
            "business_id",
            "dedupe_key",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(
        Integer, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id = Column(
        Integer, ForeignKey("clients.id", ondelete="SET NULL"), nullable=True, index=True
    )
    service_id = Column(
        Integer, ForeignKey("services.id", ondelete="CASCADE"), nullable=False, index=True
    )
    specialist_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    full_name = Column(String(200), nullable=False)
    email = Column(String(320), nullable=False, index=True)
    phone = Column(String(32), nullable=True)
    language = Column(String(2), nullable=False, default="en", server_default="en")
    preferred_date_from = Column(Date, nullable=False, index=True)
    preferred_date_to = Column(Date, nullable=True, index=True)
    time_preference = Column(
        String(16), nullable=False, default="any", server_default="any"
    )
    marketing_consent = Column(Boolean, nullable=False, default=False, server_default="false")
    processing_consent = Column(Boolean, nullable=False, default=True, server_default="true")
    status = Column(String(16), nullable=False, default="active", server_default="active", index=True)
    token_hash = Column(String(64), nullable=True, unique=True, index=True)
    dedupe_key = Column(String(64), nullable=False)
    cancelled_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class WaitlistOffer(Base):
    __tablename__ = "waitlist_offers"
    __table_args__ = (
        Index(
            "uq_waitlist_offers_pending_entry",
            "entry_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index("uq_waitlist_offers_event_key", "event_key", unique=True),
        Index("ix_waitlist_offers_business_status", "business_id", "status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(
        Integer, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entry_id = Column(
        Integer, ForeignKey("waitlist_entries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    service_id = Column(
        Integer, ForeignKey("services.id", ondelete="CASCADE"), nullable=False, index=True
    )
    specialist_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    visit_id = Column(
        Integer, ForeignKey("visits.id", ondelete="SET NULL"), nullable=True, index=True
    )
    start = Column(DateTime, nullable=False, index=True)
    end = Column(DateTime, nullable=False)
    status = Column(String(16), nullable=False, default="pending", server_default="pending", index=True)
    token_hash = Column(String(64), nullable=True, unique=True, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    event_key = Column(String(160), nullable=False)
    responded_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)
