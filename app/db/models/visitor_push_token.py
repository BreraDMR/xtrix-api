"""A phone that gets visit reminders through Expo Push.

The device belongs to one visitor session, not to the account: logging out, a password reset or
deleting the account ends the session, and the phone goes quiet together with it.
"""
from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import relationship

from app.api.core.clock import utcnow
from app.db.base import Base


class VisitorPushToken(Base):
    __tablename__ = "visitor_push_tokens"
    __table_args__ = (
        UniqueConstraint("token", name="uq_visitor_push_tokens_token"),
        CheckConstraint("platform IN ('ios','android')", name="ck_visitor_push_tokens_platform"),
        CheckConstraint("length(token) > 0", name="ck_visitor_push_tokens_token"),
    )

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("visitor_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    token = Column(String(255), nullable=False)
    platform = Column(String(16), nullable=False)
    # the reminder is written in the language the app was using when it registered
    language = Column(String(2), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow, server_default=text("timezone('utc', now())"))
    updated_at = Column(DateTime, nullable=False, default=utcnow, server_default=text("timezone('utc', now())"))

    session = relationship("VisitorSession")
