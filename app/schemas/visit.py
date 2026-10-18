from datetime import datetime
from typing import Optional, List
from app.schemas.limits import LongText, Money, ShortText
from pydantic import ConfigDict, BaseModel, Field, field_validator
from app.api.core.clock import utcnow
from app.schemas.datetimes import UtcDatetime
from app.schemas.errors import invalid, invalid_choice
from app.schemas.base import InputModel
class UsedProduct(InputModel):
    product_id: int
    quantity: float = Field(..., gt=0)
class VisitBase(BaseModel):
    client_id: int
    specialist_id: int
    service_id: Optional[int] = None
    date_start: UtcDatetime
    date_end: Optional[UtcDatetime] = None
    service_name: Optional[ShortText] = None
    note: Optional[LongText] = None
    photo_url: Optional[str] = Field(None, max_length=2048)
    price: Optional[Money] = None
    status: Optional[str] = Field("planned", description="planned|completed|cancelled|in_progress")
    payment_method: Optional[str] = Field(None, description="Payment method: card|cash|other")
    used_products: Optional[List[UsedProduct]] = Field(None, description="Products used")
    @field_validator('status')
    @classmethod
    def validate_status(cls, v):
        if v is not None and v not in ['planned', 'completed', 'cancelled', 'in_progress']:
            raise invalid_choice(['planned', 'completed', 'cancelled', 'in_progress'], "Status")
        return v
    @field_validator('payment_method')
    @classmethod
    def validate_payment_method(cls, v):
        if v is not None and v not in ['card', 'cash', 'other']:
            raise invalid_choice(['card', 'cash', 'other'], "Payment method")
        return v
class VisitCreate(InputModel, VisitBase):
    # input only - VisitOut shouldn't re-check stored rows. No 15-minute floor any more: a visit is
    # as long as its service, and services can be 1-1440 minutes
    @field_validator('date_end')
    @classmethod
    def validate_date_end(cls, v, info):
        if v is not None and isinstance(info.data.get('date_start'), datetime):
            from app.api.core.date_validation import validate_datetime_range
            is_valid, error_msg = validate_datetime_range(info.data['date_start'], v, max_duration_hours=24)
            if not is_valid:
                raise invalid("invalid_time_range", error_msg)
        return v
    @field_validator('date_start')
    @classmethod
    def validate_date_start(cls, v):
        from datetime import datetime, timedelta, timezone
        if isinstance(v, datetime):
            if v.tzinfo is not None:
                now = datetime.now(timezone.utc)
                if v.tzinfo != timezone.utc:
                    v_utc = v.astimezone(timezone.utc)
                else:
                    v_utc = v
            else:
                now = utcnow()
                v_utc = v
            if v_utc < now - timedelta(days=365):
                pass
        return v
class VisitUpdate(InputModel):
    date_start: Optional[UtcDatetime] = None
    date_end: Optional[UtcDatetime] = None
    service_id: Optional[int] = None
    service_name: Optional[ShortText] = None
    note: Optional[LongText] = None
    photo_url: Optional[str] = Field(None, max_length=2048)
    price: Optional[Money] = None
    status: Optional[str] = None
    payment_method: Optional[str] = Field(None, description="Payment method: card|cash|other")
    @field_validator('date_end')
    @classmethod
    def validate_date_end(cls, v, info):
        if v is not None and 'date_start' in info.data and info.data['date_start'] is not None:
            from app.api.core.date_validation import validate_datetime_range
            is_valid, error_msg = validate_datetime_range(info.data['date_start'], v, max_duration_hours=24)
            if not is_valid:
                raise invalid("invalid_time_range", error_msg)
        return v
    @field_validator('status')
    @classmethod
    def validate_status(cls, v):
        if v is not None and v not in ['planned', 'completed', 'cancelled', 'in_progress']:
            raise invalid_choice(['planned', 'completed', 'cancelled', 'in_progress'], "Status")
        return v
    @field_validator('payment_method')
    @classmethod
    def validate_payment_method(cls, v, info):
        if v is not None and v not in ['card', 'cash', 'other']:
            raise invalid_choice(['card', 'cash', 'other'], "Payment method")
        status = info.data.get('status') if 'status' in info.data else None
        price = info.data.get('price') if 'price' in info.data else None
        if status == 'completed' and price and price > 0:
            if not v or v not in ['card', 'cash', 'other']:
                raise invalid("payment_method_required", "Payment method is required for completed visits with price")
        return v
class VisitOut(VisitBase):
    id: int
    created_at: UtcDatetime
    model_config = ConfigDict(from_attributes=True)
