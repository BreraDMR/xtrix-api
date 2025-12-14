"""Date and time validation utilities."""
from datetime import datetime, date, time, timedelta
from typing import Optional, Tuple


def validate_datetime_range(
    start: datetime,
    end: Optional[datetime] = None,
    min_duration_minutes: Optional[int] = None,
    max_duration_hours: Optional[int] = None,
) -> Tuple[bool, str]:
    """
    Validate datetime range.
    Returns (is_valid, error_message)
    """
    now = datetime.utcnow()
    
    # Check if start is in the past (for visits, allow past dates for historical records)
    # For future appointments, start should be in the future
    # We'll allow past dates but warn if needed
    
    if end is not None:
        if end <= start:
            return False, "End date/time must be after start date/time"
        
        duration = end - start
        
        # Check minimum duration
        if min_duration_minutes is not None:
            min_duration = timedelta(minutes=min_duration_minutes)
            if duration < min_duration:
                return False, f"Duration must be at least {min_duration_minutes} minutes"
        
        # Check maximum duration
        if max_duration_hours is not None:
            max_duration = timedelta(hours=max_duration_hours)
            if duration > max_duration:
                return False, f"Duration cannot exceed {max_duration_hours} hours"
        
        # Check if duration is too long (e.g., more than 24 hours for a visit)
        if duration > timedelta(days=1):
            return False, "Duration cannot exceed 24 hours"
    
    return True, ""


def validate_time_range(
    start_time: time,
    end_time: time,
) -> Tuple[bool, str]:
    """
    Validate time range (for work schedules).
    Returns (is_valid, error_message)
    """
    if end_time <= start_time:
        return False, "End time must be after start time"
    
    # Check if duration is reasonable (at least 1 hour, max 24 hours)
    start_dt = datetime.combine(date.today(), start_time)
    end_dt = datetime.combine(date.today(), end_time)
    
    if end_dt <= start_dt:
        # Handle case where end_time is next day
        end_dt = datetime.combine(date.today() + timedelta(days=1), end_time)
    
    duration = end_dt - start_dt
    
    if duration < timedelta(hours=1):
        return False, "Time range must be at least 1 hour"
    
    if duration > timedelta(hours=24):
        return False, "Time range cannot exceed 24 hours"
    
    return True, ""


def validate_date_not_future(check_date: date) -> Tuple[bool, str]:
    """
    Validate that date is not in the future.
    Returns (is_valid, error_message)
    """
    if check_date > date.today():
        return False, "Date cannot be in the future"
    return True, ""


def validate_date_not_past(check_date: date) -> Tuple[bool, str]:
    """
    Validate that date is not in the past.
    Returns (is_valid, error_message)
    """
    if check_date < date.today():
        return False, "Date cannot be in the past"
    return True, ""


def validate_datetime_not_past(check_datetime: datetime) -> Tuple[bool, str]:
    """
    Validate that datetime is not in the past (for future appointments).
    Returns (is_valid, error_message)
    """
    now = datetime.utcnow()
    if check_datetime < now:
        return False, "Date and time cannot be in the past"
    return True, ""


def validate_birth_date(birth_date: date) -> Tuple[bool, str]:
    """
    Validate birth date (must be in the past and reasonable age).
    Returns (is_valid, error_message)
    """
    today = date.today()
    
    if birth_date > today:
        return False, "Birth date cannot be in the future"
    
    # Check if age is reasonable (between 0 and 150 years)
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
    """
    Validate that datetime is within business hours.
    Returns (is_valid, error_message)
    """
    hour = check_datetime.hour
    
    if hour < start_hour or hour >= end_hour:
        return False, f"Time must be between {start_hour}:00 and {end_hour}:00"
    
    return True, ""









