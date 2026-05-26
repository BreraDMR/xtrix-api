from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator
class BlockSlotBase(BaseModel):
    start: datetime
    end: datetime
    reason: Optional[str] = Field(None, max_length=500)
    @field_validator('end')
    @classmethod
    def validate_datetime_range(cls, v, info):
        if 'start' in info.data:
            from app.api.core.date_validation import validate_datetime_range
            is_valid, error_msg = validate_datetime_range(
                info.data['start'],
                v,
                min_duration_minutes=15,  
                max_duration_hours=24,     
            )
            if not is_valid:
                raise ValueError(error_msg)
        return v
class BlockSlotCreate(BlockSlotBase):
    specialist_id: int
class BlockSlotUpdate(BaseModel):
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    reason: Optional[str] = None
class BlockSlotOut(BlockSlotBase):
    id: int
    specialist_id: int
    class Config:
        from_attributes = True
