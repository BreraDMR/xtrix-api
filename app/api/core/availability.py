"""The one availability calculation for public booking (and later CAL/MULTI).

Inputs are already loaded from the database; this module only does arithmetic. Business
hours and date exceptions are local wall-clock values in the salon's timezone, while
visits and blocks are naive UTC. Candidate slots are built in local time, checked in UTC,
and returned as naive UTC so they serialize with a trailing Z.
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from app.api.core.clock import utcnow

UTC = timezone.utc
SLOT_STEP_MINUTES = 15
MAX_SLOTS = 500
MAX_HORIZON_DAYS = 3650

ScheduleMap = Mapping[int, Sequence[tuple[int, time, time]]]
# staff -> date -> (start, end); (None, None) means that master's day is closed
ExceptionMap = Mapping[int, Mapping[date, tuple[Optional[time], Optional[time]]]]
RangeMap = Mapping[int, Sequence[tuple[datetime, datetime]]]


@dataclass(frozen=True)
class AvailableSlot:
    day: date
    start: datetime  # naive UTC
    staff_id: int


def salon_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


def booking_window(
    date_from: date,
    date_to: date,
    timezone_name: str,
    horizon_days: int,
    now: Optional[datetime] = None,
) -> tuple[date, date, date]:
    """Clamp a requested range to today..horizon in the salon's timezone."""
    zone = salon_zone(timezone_name)
    current = now or utcnow()
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    else:
        current = current.astimezone(UTC)
    today = current.astimezone(zone).date()
    horizon = max(0, min(int(horizon_days or 0), MAX_HORIZON_DAYS))
    first = max(date_from, today)
    last = min(date_to, today + timedelta(days=horizon))
    return first, last, today


def _overlaps(start: datetime, end: datetime, ranges: Sequence[tuple[datetime, datetime]]) -> bool:
    return any(start < other_end and other_start < end for other_start, other_end in ranges)


def collect_slots(
    *,
    date_from: date,
    date_to: date,
    timezone_name: str,
    horizon_days: int,
    lead_time_minutes: int,
    duration_minutes: int,
    buffer_before_minutes: int = 0,
    buffer_after_minutes: int = 0,
    master_ids: Sequence[int],
    schedules: ScheduleMap,
    exceptions: ExceptionMap,
    blocks: RangeMap,
    busy: RangeMap,
    now: Optional[datetime] = None,
    max_slots: int = MAX_SLOTS,
) -> list[AvailableSlot]:
    zone = salon_zone(timezone_name)
    first, last, _today = booking_window(
        date_from, date_to, timezone_name, horizon_days, now=now
    )
    if last < first or not master_ids:
        return []

    current = now or utcnow()
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    else:
        current = current.astimezone(UTC)
    lead_until = (current + timedelta(minutes=max(0, lead_time_minutes))).replace(tzinfo=None)

    duration = timedelta(minutes=max(1, duration_minutes))
    before = timedelta(minutes=max(0, buffer_before_minutes))
    after = timedelta(minutes=max(0, buffer_after_minutes))
    step = timedelta(minutes=SLOT_STEP_MINUTES)

    # day -> start (naive UTC) -> staff id, so overlapping windows and DST folds don't duplicate
    collected: dict[date, dict[datetime, int]] = {}
    total = 0
    stopped = False

    for offset in range((last - first).days + 1):
        day = first + timedelta(days=offset)
        for staff_id in master_ids:
            staff_exceptions = exceptions.get(staff_id, {})
            if day in staff_exceptions and staff_exceptions[day][0] is None:
                continue
            if day in staff_exceptions:
                exception_start, exception_end = staff_exceptions[day]
                windows: list[tuple[time, time]] = (
                    [] if exception_start is None or exception_end is None
                    else [(exception_start, exception_end)]
                )
            else:
                windows = [
                    (start, end)
                    for weekday, start, end in schedules.get(staff_id, ())
                    if weekday == day.weekday()
                ]
            for window_start, window_end in windows:
                cursor = datetime.combine(day, window_start)
                end_local = datetime.combine(day, window_end)
                if end_local <= cursor:
                    continue
                while cursor + duration <= end_local:
                    local = cursor.replace(tzinfo=zone)
                    aware_utc = local.astimezone(UTC)
                    start = aware_utc.replace(tzinfo=None)
                    # a wall time inside the spring-forward gap doesn't exist; skip it
                    if aware_utc.astimezone(zone).replace(tzinfo=None) == cursor and start >= lead_until:
                        conflict_start = start - before
                        conflict_end = start + duration + after
                        if (
                            not _overlaps(conflict_start, conflict_end, blocks.get(staff_id, ()))
                            and not _overlaps(conflict_start, conflict_end, busy.get(staff_id, ()))
                        ):
                            day_bucket = collected.setdefault(day, {})
                            if start not in day_bucket:
                                day_bucket[start] = staff_id
                                total += 1
                                if total >= max_slots:
                                    stopped = True
                                    break
                    cursor += step
                if stopped:
                    break
            if stopped:
                break
        if stopped:
            break

    slots = [
        AvailableSlot(day=day, start=start, staff_id=staff_id)
        for day in sorted(collected)
        for start, staff_id in sorted(collected[day].items(), key=lambda item: (item[0], item[1]))
    ]
    return slots[:max_slots]
