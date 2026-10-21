from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.api.core.clock import utcnow
from app.db.base import Base


class PublicBookingSettings(Base):
    """The public face of a business: a slug and an explicit on/off switch.

    A row is created lazily for every business that opens the settings screen, always
    disabled until the owner turns booking on.
    """
    __tablename__ = "public_booking_settings"

    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(
        Integer,
        ForeignKey("businesses.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    slug = Column(String(64), nullable=False, unique=True, index=True)
    is_enabled = Column(Boolean, nullable=False, default=False, server_default="false")
    # deposit policy for guest bookings; "none" keeps the old pay-at-the-salon flow
    deposit_type = Column(String(16), nullable=False, default="none", server_default="none")
    deposit_value = Column(Numeric(10, 2), nullable=False, default=0, server_default="0")
    deposit_hold_minutes = Column(Integer, nullable=False, default=30, server_default="30")
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    business = relationship("Business", back_populates="public_booking_settings", lazy="select")


class PublicBookingRequest(Base):
    """One row per submitted retry key, so a lost response never creates a second visit."""
    __tablename__ = "public_booking_requests"
    __table_args__ = (
        UniqueConstraint("business_id", "request_id", name="uq_public_booking_request"),
    )

    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=False, index=True)
    client_id = Column(
        Integer,
        ForeignKey("clients.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    visit_id = Column(
        Integer,
        ForeignKey("visits.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    request_id = Column(String(64), nullable=False)
    request_hash = Column(String(64), nullable=False)
    marketing_consent = Column(Boolean, nullable=False, default=False, server_default="false")
    processing_consent = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(DateTime, nullable=False, default=utcnow, index=True)
