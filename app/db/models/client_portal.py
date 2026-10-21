"""Visitor-portal credentials: one-time magic links and opaque client sessions.

Both tables keep only SHA-256 digests. A session is always scoped to one business and
one client, so the same person in two salons has two unrelated credentials.
"""
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String

from app.api.core.clock import utcnow
from app.db.base import Base


class ClientPortalToken(Base):
    __tablename__ = "client_portal_tokens"

    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(
        Integer, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id = Column(
        Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class ClientPortalSession(Base):
    __tablename__ = "client_portal_sessions"

    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(
        Integer, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id = Column(
        Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    revoked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
