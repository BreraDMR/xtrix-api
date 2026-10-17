from sqlalchemy import Column, DateTime, Integer, String, Boolean, ForeignKey, JSON
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.api.core.clock import utcnow
class UserPreferences(Base):
    __tablename__ = "user_preferences"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    timezone = Column(String, nullable=True, default="UTC")  
    date_format = Column(String, nullable=True, default="DD/MM/YYYY")  
    time_format = Column(String, nullable=True, default="24h")  
    email_notifications = Column(Boolean, default=True, nullable=False)
    push_notifications = Column(Boolean, default=True, nullable=False)
    notification_email = Column(String, nullable=True)  
    theme = Column(String, nullable=False, default="light")  
    auto_save_enabled = Column(Boolean, default=True, nullable=False)
    auto_save_interval = Column(Integer, default=30, nullable=False)  
    export_format = Column(String, nullable=True, default="csv")  
    language = Column(String(2), nullable=True)  # staff UI language, used for e-mails to this user
    additional_settings = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=utcnow, index=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, index=True)
    user = relationship("User", back_populates="preferences", lazy="select")
