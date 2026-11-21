"""Per-business notification switches, channel and reminder time."""
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String

from app.api.core.clock import utcnow
from app.db.base import Base


class NotificationSettings(Base):
    __tablename__ = "notification_settings"

    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(
        Integer,
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    channel = Column(String(20), nullable=False, default="email", server_default="email")
    reminder_time = Column(String(5), nullable=False, default="10:00", server_default="10:00")
    is_enabled = Column(Boolean, nullable=False, default=False, server_default="false")
    # marketing messages: off until the owner turns them on, and they still need client consent
    birthday_greetings_enabled = Column(Boolean, nullable=False, default=False, server_default="false")
    visit_followups_enabled = Column(Boolean, nullable=False, default=False, server_default="false")
    # confirmations of visits the staff created, moved or cancelled in the CRM
    crm_visit_messages_enabled = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)
