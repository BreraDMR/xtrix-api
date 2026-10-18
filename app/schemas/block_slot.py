from typing import Optional
from pydantic import ConfigDict, BaseModel, Field, field_validator
from app.schemas.datetimes import UtcDatetime
from app.schemas.errors import invalid
from app.schemas.base import InputModel
class BlockSlotBase(BaseModel):
    start: UtcDatetime
    end: UtcDatetime
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
                raise invalid("invalid_time_range", error_msg)
        return v
class BlockSlotCreate(InputModel, BlockSlotBase):
    specialist_id: int
class BlockSlotUpdate(InputModel):
    start: Optional[UtcDatetime] = None
    end: Optional[UtcDatetime] = None
    reason: Optional[str] = None
class BlockSlotOut(BlockSlotBase):
    id: int
    specialist_id: int
    model_config = ConfigDict(from_attributes=True)
