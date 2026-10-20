from datetime import date, time
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.schemas.base import InputModel
from app.schemas.errors import invalid


def _validate_window(is_closed: bool, start_time: time | None, end_time: time | None) -> None:
    if is_closed:
        if start_time is not None or end_time is not None:
            raise invalid("closed_day_has_hours", "A closed day cannot have working hours")
        return
    if start_time is None or end_time is None:
        raise invalid("exception_hours_required", "Custom working hours need both start and end")
    from app.api.core.date_validation import validate_time_range
    is_valid, message = validate_time_range(start_time, end_time)
    if not is_valid:
        raise invalid("invalid_time_range", message)


class ScheduleExceptionBase(BaseModel):
    exception_date: date
    is_closed: bool = True
    start_time: time | None = None
    end_time: time | None = None
    reason: str | None = Field(None, max_length=255)

    @model_validator(mode="after")
    def validate_day(self):
        _validate_window(self.is_closed, self.start_time, self.end_time)
        return self


class ScheduleExceptionCreate(InputModel, ScheduleExceptionBase):
    specialist_id: int = Field(..., gt=0)


class ScheduleExceptionUpdate(InputModel):
    exception_date: date | None = None
    is_closed: bool | None = None
    start_time: time | None = None
    end_time: time | None = None
    reason: str | None = Field(None, max_length=255)

    @model_validator(mode="after")
    def reject_null_required(self):
        # clearing the day is done with is_closed; a date cannot be unset
        for name in ("exception_date", "is_closed"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise invalid(f"{name}_required", f"{name} cannot be null")
        return self


class ScheduleExceptionOut(ScheduleExceptionBase):
    id: int
    specialist_id: int
    model_config = ConfigDict(from_attributes=True)
