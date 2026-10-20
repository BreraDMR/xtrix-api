from sqlalchemy import Column, DateTime, Integer, String, ForeignKey

from sqlalchemy.orm import relationship

from app.db.base import Base
from app.api.core.clock import utcnow


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # only the SHA-256 digest is stored; the raw token exists in the email link
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    user = relationship("User", backref="password_reset_tokens")
