from datetime import datetime
from sqlalchemy import Column, DateTime, Integer, String, Boolean, ForeignKey, JSON
from sqlalchemy.orm import relationship

from app.db.base import Base


class UserPreferences(Base):
    """
    Модель для хранения настроек пользователя.
    """
    __tablename__ = "user_preferences"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    
    # Часовой пояс
    timezone = Column(String, nullable=True, default="UTC")  # Например: "Europe/Prague", "America/New_York"
    
    # Формат даты/времени
    date_format = Column(String, nullable=True, default="DD/MM/YYYY")  # DD/MM/YYYY, MM/DD/YYYY, YYYY-MM-DD
    time_format = Column(String, nullable=True, default="24h")  # 24h, 12h
    
    # Уведомления
    email_notifications = Column(Boolean, default=True, nullable=False)
    push_notifications = Column(Boolean, default=True, nullable=False)
    notification_email = Column(String, nullable=True)  # Альтернативный email для уведомлений
    
    # Тема оформления
    theme = Column(String, nullable=False, default="light")  # light, dark, system
    
    # Автосохранение
    auto_save_enabled = Column(Boolean, default=True, nullable=False)
    auto_save_interval = Column(Integer, default=30, nullable=False)  # Интервал в секундах
    
    # Экспорт данных
    export_format = Column(String, nullable=True, default="csv")  # csv, excel, json
    
    # Дополнительные настройки (JSON для расширяемости)
    additional_settings = Column(JSON, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)
    
    # Relationships
    user = relationship("User", back_populates="preferences", lazy="select")
