from sqlalchemy import Column, DateTime, ForeignKey, Integer, String

from app.api.core.clock import utcnow
from app.db.base import Base


class GoogleIdentity(Base):
    __tablename__ = "google_identities"

    subject = Column(String(255), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class GoogleLoginNonce(Base):
    __tablename__ = "google_login_nonces"

    nonce_hash = Column(String(64), primary_key=True)
    origin = Column(String(2048), nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    used_at = Column(DateTime, nullable=True)
