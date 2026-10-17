from sqlalchemy import Column, DateTime, Integer, String, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.api.core.clock import utcnow
class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id = Column(Integer, primary_key=True, index=True)
    token = Column(String, unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    is_revoked = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    user = relationship("User", back_populates="refresh_tokens")
