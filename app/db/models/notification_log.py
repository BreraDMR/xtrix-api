from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint
from app.db.base import Base
from app.api.core.clock import utcnow
class NotificationLog(Base):
    __tablename__ = "notification_log"
    __table_args__ = (UniqueConstraint("kind", "key", name="uq_notification_log_kind_key"),)
    id = Column(Integer, primary_key=True)
    kind = Column(String(50), nullable=False)
    key = Column(String(200), nullable=False)
    created_at = Column(DateTime, nullable=False, default=utcnow)
