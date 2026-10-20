"""Hashed bearer tokens that let one visit's guest confirm, move or cancel it."""
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint

from app.api.core.clock import utcnow
from app.db.base import Base


class BookingManageToken(Base):
    __tablename__ = "booking_manage_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_booking_manage_token_hash"),
    )

    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(Integer, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True)
    visit_id = Column(Integer, ForeignKey("visits.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = Column(String(64), nullable=False)
    actions = Column(
        String(64),
        nullable=False,
        default="confirm,reschedule,cancel",
        server_default="confirm,reschedule,cancel",
    )
    expires_at = Column(DateTime, nullable=False, index=True)
    revoked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
