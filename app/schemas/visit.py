from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field, field_validator


class UsedProduct(BaseModel):
    """Товар, использованный при визите."""
    product_id: int
    quantity: float = Field(..., gt=0)


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
    payment_method: Optional[str] = Field(None, description="card|cash|other - способ оплаты")
    used_products: Optional[List[UsedProduct]] = Field(None, description="Список использованных товаров")
    
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
    
    @field_validator('payment_method')
    @classmethod
    def validate_payment_method(cls, v):
        # Проверяем только валидность значения, если оно указано
        # Не требуем payment_method для чтения существующих данных
        if v is not None and v not in ['card', 'cash', 'other']:
            raise ValueError("Payment method must be one of: card, cash, other")
        return v


class VisitCreate(VisitBase):
    @field_validator('date_start')
    @classmethod
    def validate_date_start(cls, v):
        # Allow past dates for historical records, but warn if too far in the past
        from datetime import datetime, timedelta, timezone
        
        # Нормализуем timezone для сравнения
        # Если v timezone-aware, сравниваем с aware now
        # Если v timezone-naive, сравниваем с naive now
        if isinstance(v, datetime):
            if v.tzinfo is not None:
                # v is timezone-aware, use timezone-aware now
                now = datetime.now(timezone.utc)
                # Normalize v to UTC for comparison
                if v.tzinfo != timezone.utc:
                    v_utc = v.astimezone(timezone.utc)
                else:
                    v_utc = v
            else:
                # v is timezone-naive, use naive now
                now = datetime.utcnow()
                v_utc = v
            
            if v_utc < now - timedelta(days=365):
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
    payment_method: Optional[str] = Field(None, description="card|cash|other - способ оплаты")
    
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
    
    @field_validator('payment_method')
    @classmethod
    def validate_payment_method(cls, v, info):
        # Проверяем валидность значения, если оно указано
        if v is not None and v not in ['card', 'cash', 'other']:
            raise ValueError("Payment method must be one of: card, cash, other")
        
        # При обновлении: если статус изменяется на completed и указана цена, требуется payment_method
        # Но только если status передается в обновлении
        status = info.data.get('status') if 'status' in info.data else None
        price = info.data.get('price') if 'price' in info.data else None
        
        # Если это обновление, нужно проверить текущий статус визита
        # Но это сложно без доступа к существующему визиту, поэтому проверяем только переданные данные
        if status == 'completed' and price and price > 0:
            if not v or v not in ['card', 'cash', 'other']:
                raise ValueError("Payment method is required for completed visits with price")
        
        return v


class VisitOut(VisitBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True
