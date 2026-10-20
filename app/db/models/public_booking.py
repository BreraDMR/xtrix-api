from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String
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
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    business = relationship("Business", back_populates="public_booking_settings", lazy="select")
