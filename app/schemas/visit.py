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
            from app.api.core.date_validation import validate_datetime_range
            is_valid, error_msg = validate_datetime_range(
                info.data['date_start'],
                v,
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
        from datetime import timedelta, timezone
        # Make both datetimes offset-aware for comparison
        now = datetime.now(timezone.utc)
        # If v is offset-naive, make it offset-aware
        if v.tzinfo is None:
            # Assume UTC if no timezone info
            v = v.replace(tzinfo=timezone.utc)
        elif v.tzinfo.utcoffset(v) is None:
            # If it's a timezone-aware but offset is None, treat as UTC
            v = v.replace(tzinfo=timezone.utc)
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
