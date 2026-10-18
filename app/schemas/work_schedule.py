from datetime import time
from pydantic import ConfigDict, BaseModel, Field, field_validator
from app.schemas.errors import invalid
from app.schemas.base import InputModel
class WorkScheduleBase(BaseModel):
    weekday: int = Field(..., ge=0, le=6, description="0=Monday, 6=Sunday")
    start_time: time
    end_time: time
    @field_validator('end_time')
    @classmethod
    def validate_time_range(cls, v, info):
        if 'start_time' in info.data:
            from app.api.core.date_validation import validate_time_range
            is_valid, error_msg = validate_time_range(
                info.data['start_time'],
                v
            )
            if not is_valid:
                raise invalid("invalid_time_range", error_msg)
        return v
class WorkScheduleCreate(InputModel, WorkScheduleBase):
    specialist_id: int
class WorkScheduleUpdate(InputModel):
    start_time: time | None = None
    end_time: time | None = None
class WorkScheduleOut(WorkScheduleBase):
    id: int
    specialist_id: int
    model_config = ConfigDict(from_attributes=True)
