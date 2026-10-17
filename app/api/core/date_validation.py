from datetime import datetime, date, time, timedelta
from typing import Optional, Tuple
from app.api.core.clock import utcnow
def validate_datetime_range(
    start: datetime,
    end: Optional[datetime] = None,
    min_duration_minutes: Optional[int] = None,
    max_duration_hours: Optional[int] = None,
) -> Tuple[bool, str]:
    now = utcnow()
    if end is not None:
        if end <= start:
            return False, "End date/time must be after start date/time"
        duration = end - start
        if min_duration_minutes is not None:
            min_duration = timedelta(minutes=min_duration_minutes)
            if duration < min_duration:
                return False, f"Duration must be at least {min_duration_minutes} minutes"
        if max_duration_hours is not None:
            max_duration = timedelta(hours=max_duration_hours)
            if duration > max_duration:
                return False, f"Duration cannot exceed {max_duration_hours} hours"
        if duration > timedelta(days=1):
            return False, "Duration cannot exceed 24 hours"
    return True, ""
def validate_time_range(
    start_time: time,
    end_time: time,
) -> Tuple[bool, str]:
    if end_time <= start_time:
        return False, "End time must be after start time"
    start_dt = datetime.combine(date.today(), start_time)
    end_dt = datetime.combine(date.today(), end_time)
    if end_dt <= start_dt:
        end_dt = datetime.combine(date.today() + timedelta(days=1), end_time)
    duration = end_dt - start_dt
    if duration < timedelta(hours=1):
        return False, "Time range must be at least 1 hour"
    if duration > timedelta(hours=24):
        return False, "Time range cannot exceed 24 hours"
    return True, ""
def validate_date_not_future(check_date: date) -> Tuple[bool, str]:
    if check_date > date.today():
        return False, "Date cannot be in the future"
    return True, ""
def validate_date_not_past(check_date: date) -> Tuple[bool, str]:
    if check_date < date.today():
        return False, "Date cannot be in the past"
    return True, ""
def validate_datetime_not_past(check_datetime: datetime) -> Tuple[bool, str]:
    now = utcnow()
    if check_datetime < now:
        return False, "Date and time cannot be in the past"
    return True, ""
def validate_birth_date(birth_date: date) -> Tuple[bool, str]:
    today = date.today()
    if birth_date > today:
        return False, "Birth date cannot be in the future"
    age = today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
    if age < 0:
        return False, "Birth date cannot be in the future"
    if age > 150:
        return False, "Birth date indicates an unrealistic age (over 150 years)"
    return True, ""
def validate_datetime_business_hours(
    check_datetime: datetime,
    start_hour: int = 9,
    end_hour: int = 18,
) -> Tuple[bool, str]:
    hour = check_datetime.hour
    if hour < start_hour or hour >= end_hour:
        return False, f"Time must be between {start_hour}:00 and {end_hour}:00"
    return True, ""
