from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class VisitBase(BaseModel):
    client_id: int
    specialist_id: int
    service_id: Optional[int] = None
    date_start: datetime
    date_end: Optional[datetime] = None
    service_name: Optional[str] = None
    note: Optional[str] = None
    photo_url: Optional[str] = None
    price: Optional[float] = Field(None, ge=0.0)
    status: Optional[str] = Field("planned", description="planned|completed|cancelled|in_progress")
    
    @field_validator('date_end')
    @classmethod
    def validate_date_end(cls, v, info):
        if v is not None and 'date_start' in info.data:
            from datetime import datetime
            date_start = info.data['date_start']
            date_end = v
            
            # Нормализуем timezone для сравнения
            # Если один datetime aware, а другой naive, делаем оба naive для сравнения
            if isinstance(date_start, datetime) and isinstance(date_end, datetime):
                if (date_start.tzinfo is not None) != (date_end.tzinfo is not None):
                    # Один aware, другой naive - делаем оба naive для сравнения
                    if date_start.tzinfo is not None:
                        date_start = date_start.replace(tzinfo=None)
                    if date_end.tzinfo is not None:
                        date_end = date_end.replace(tzinfo=None)
            
            from app.api.core.date_validation import validate_datetime_range
            is_valid, error_msg = validate_datetime_range(
                date_start,
                date_end,
                min_duration_minutes=15,  # Minimum 15 minutes
                max_duration_hours=24,    # Maximum 24 hours
            )
            if not is_valid:
                raise ValueError(error_msg)
        return v
    
    @field_validator('status')
    @classmethod
    def validate_status(cls, v):
        if v is not None and v not in ['planned', 'completed', 'cancelled', 'in_progress']:
            raise ValueError("Status must be one of: planned, completed, cancelled, in_progress")
        return v


class VisitCreate(VisitBase):
    @field_validator('date_start')
    @classmethod
    def validate_date_start(cls, v):
        # Allow past dates for historical records, but warn if too far in the past
        from datetime import datetime, timedelta
        now = datetime.utcnow()
        if v < now - timedelta(days=365):
            # Allow but could warn
            pass
        return v


class VisitUpdate(BaseModel):
    date_start: Optional[datetime] = None
    date_end: Optional[datetime] = None
    service_id: Optional[int] = None
    service_name: Optional[str] = None
    note: Optional[str] = None
    photo_url: Optional[str] = None
    price: Optional[float] = Field(None, ge=0.0)
    status: Optional[str] = None
    
    @field_validator('date_end')
    @classmethod
    def validate_date_end(cls, v, info):
        if v is not None and 'date_start' in info.data and info.data['date_start'] is not None:
            from app.api.core.date_validation import validate_datetime_range
            is_valid, error_msg = validate_datetime_range(
                info.data['date_start'],
                v,
                min_duration_minutes=15,
                max_duration_hours=24,
            )
            if not is_valid:
                raise ValueError(error_msg)
        return v
    
    @field_validator('status')
    @classmethod
    def validate_status(cls, v):
        if v is not None and v not in ['planned', 'completed', 'cancelled', 'in_progress']:
            raise ValueError("Status must be one of: planned, completed, cancelled, in_progress")
        return v


class VisitOut(VisitBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True
