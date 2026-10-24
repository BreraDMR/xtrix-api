"""Internal slot search uses the same schedule, item buffers and UTC rules as booking."""
from datetime import datetime, time, timedelta, timezone

from sqlalchemy.orm import Session

from app.api.core.availability import ChainTiming, _utc_window, collect_chain_slots, salon_zone
from app.api.core.clock import utcnow
from app.api.core.permissions import get_user_business_id
from app.api.crud.crud_public_booking import availability_sources
from app.db.models.business import Business
from app.db.models.user import User
from app.db.models.user_preferences import UserPreferences


def find_staff_slot(db: Session, specialist_id: int, *, service=None, duration_minutes=60,
                    start_from=None, days_ahead=7, now=None, timezone_name=None,
                    work_start: time | None = None, work_end: time | None = None, breaks=None):
    staff = db.get(User, specialist_id)
    if staff is None:
        return None
    business_id = get_user_business_id(staff, db)
    business = db.get(Business, business_id) if business_id else None
    preferences = db.query(UserPreferences).filter(UserPreferences.user_id == specialist_id).first()
    zone_name = (business.timezone if business else None) or (preferences.timezone if preferences else None) or timezone_name or 'UTC'
    zone = salon_zone(zone_name)
    current = now or utcnow()
    current = current.astimezone(timezone.utc).replace(tzinfo=None) if current.tzinfo else current
    first_start = max(current, start_from or current)
    first = first_start.replace(tzinfo=timezone.utc).astimezone(zone).date()
    last = first + timedelta(days=days_ahead - 1)
    schedules, exceptions, blocks, busy = availability_sources(db, [specialist_id], first, last, zone_name)
    # Explicit legacy parameters can narrow working hours, never create or expand them.
    def clipped(start, end):
        left, right = max(start, work_start or start), min(end, work_end or end)
        return (left, right) if left < right else (None, None)
    schedules[specialist_id] = sorted((weekday, left, right)
        for weekday, start, end in schedules.get(specialist_id, [])
        for left, right in [clipped(start, end)] if left is not None)
    exceptions[specialist_id] = {day: clipped(start, end) if start is not None and end is not None else (None, None)
        for day, (start, end) in exceptions.get(specialist_id, {}).items()}
    for offset in range(days_ahead):
        for start, end in breaks or []:
            blocks.setdefault(specialist_id, []).extend(_utc_window(first + timedelta(days=offset), start, end, zone))
    timings = [ChainTiming(duration_minutes, service.buffer_before_minutes or 0 if service else 0,
                           service.buffer_after_minutes or 0 if service else 0)]
    slots = collect_chain_slots(date_from=first, date_to=last, timezone_name=zone_name,
        horizon_days=business.booking_horizon_days if business else max(0, (last - current.replace(tzinfo=timezone.utc).astimezone(zone).date()).days),
        lead_time_minutes=business.booking_lead_time_minutes if business else 0, timings=timings,
        master_ids=[specialist_id], schedules=schedules, exceptions=exceptions, blocks=blocks, busy=busy, now=current)
    return min((slot.start for slot in slots if slot.start >= first_start), default=None)
