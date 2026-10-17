from typing import Optional, Dict, Any
from datetime import datetime
from pydantic import ConfigDict, BaseModel, EmailStr, Field, field_validator
from typing import Literal
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
    # same values the Out model accepts - otherwise garbage got saved and then blew up the response
    timezone: Optional[str] = Field(None, max_length=64)
    date_format: Optional[Literal["DD/MM/YYYY", "MM/DD/YYYY", "YYYY-MM-DD"]] = None
    time_format: Optional[Literal["24h", "12h"]] = None
    email_notifications: Optional[bool] = None
    push_notifications: Optional[bool] = None
    notification_email: Optional[EmailStr] = None
    theme: Optional[Literal["light", "dark", "system"]] = None
    auto_save_enabled: Optional[bool] = None
    auto_save_interval: Optional[int] = Field(None, ge=5, le=300)
    export_format: Optional[Literal["csv", "excel", "json"]] = None
    additional_settings: Optional[Dict[str, Any]] = None
    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, v):
        if v is None:
            return v
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(v)
        except Exception:
            raise ValueError("Unknown timezone, use an IANA name like Europe/Prague")
        return v
class UserPreferencesOut(UserPreferencesBase):
    id: int
    user_id: int
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)
