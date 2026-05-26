from typing import Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field
class UserPreferencesBase(BaseModel):
    timezone: Optional[str] = Field(None, description="Часовой пояс (например: Europe/Prague)")
    date_format: Optional[str] = Field("DD/MM/YYYY", description="Формат даты: DD/MM/YYYY, MM/DD/YYYY, YYYY-MM-DD")
    time_format: Optional[str] = Field("24h", description="Формат времени: 24h, 12h")
    email_notifications: bool = Field(True, description="Email уведомления")
    push_notifications: bool = Field(True, description="Push уведомления")
    notification_email: Optional[str] = Field(None, description="Альтернативный email для уведомлений")
    theme: str = Field("light", description="Тема оформления: light, dark, system")
    auto_save_enabled: bool = Field(True, description="Автосохранение форм")
    auto_save_interval: int = Field(30, ge=5, le=300, description="Интервал автосохранения в секундах")
    export_format: Optional[str] = Field("csv", description="Формат экспорта: csv, excel, json")
class UserPreferencesUpdate(BaseModel):
    timezone: Optional[str] = None
    date_format: Optional[str] = None
    time_format: Optional[str] = None
    email_notifications: Optional[bool] = None
    push_notifications: Optional[bool] = None
    notification_email: Optional[str] = None
    theme: Optional[str] = None
    auto_save_enabled: Optional[bool] = None
    auto_save_interval: Optional[int] = None
    export_format: Optional[str] = None
    additional_settings: Optional[Dict[str, Any]] = None
class UserPreferencesOut(UserPreferencesBase):
    id: int
    user_id: int
    created_at: datetime
    updated_at: datetime
    class Config:
        from_attributes = True
