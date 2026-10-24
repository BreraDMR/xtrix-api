"""The one availability calculation for public booking (and later CAL/MULTI).

Inputs are already loaded from the database; this module only does arithmetic. Business
hours and date exceptions are local wall-clock values in the salon's timezone, while
visits and blocks are naive UTC. Candidate slots are built in local time, checked in UTC,
and returned as naive UTC so they serialize with a trailing Z.
"""
from dataclasses import dataclass
from functools import lru_cache
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


@dataclass(frozen=True)
class ChainTiming:
    duration_minutes: int
    buffer_before_minutes: int = 0
    buffer_after_minutes: int = 0


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
    return collect_chain_slots(
        date_from=date_from, date_to=date_to, timezone_name=timezone_name,
        horizon_days=horizon_days, lead_time_minutes=lead_time_minutes,
        timings=[ChainTiming(duration_minutes, buffer_before_minutes, buffer_after_minutes)],
        master_ids=master_ids, schedules=schedules, exceptions=exceptions,
        blocks=blocks, busy=busy, now=now, max_slots=max_slots,
    )


def _windows_for_day(
    staff_id: int,
    day: date,
    schedules: ScheduleMap,
    exceptions: ExceptionMap,
) -> list[tuple[time, time]]:
    staff_exceptions = exceptions.get(staff_id, {})
    if day in staff_exceptions:
        start, end = staff_exceptions[day]
        return [] if start is None or end is None else [(start, end)]
    return [
        (start, end)
        for weekday, start, end in schedules.get(staff_id, ())
        if weekday == day.weekday()
    ]


@lru_cache(maxsize=4096)
def _utc_window(day: date, start: time, end: time, zone: ZoneInfo):
    """Project a wall-clock window onto real intervals, including both DST folds."""
    wall_start, wall_end = datetime.combine(day, start), datetime.combine(day, end)
    if wall_end <= wall_start:
        return ()
    first = datetime.combine(day, time.min, tzinfo=zone).astimezone(UTC)
    last = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone).astimezone(UTC)
    cursor = segment_start = first
    offset = first.astimezone(zone).utcoffset()
    segments = []
    # Modern timezone transitions are minute-aligned; bounds keep schedule seconds intact.
    while cursor < last:
        next_cursor = min(cursor + timedelta(minutes=1), last)
        next_offset = next_cursor.astimezone(zone).utcoffset()
        if next_offset != offset or next_cursor == last:
            left = max(segment_start.replace(tzinfo=None), wall_start - offset)
            right = min(next_cursor.replace(tzinfo=None), wall_end - offset)
            if left < right:
                if segments and segments[-1][1] == left:
                    segments[-1] = (segments[-1][0], right)
                else:
                    segments.append((left, right))
            segment_start, offset = next_cursor, next_offset
        cursor = next_cursor
    return tuple(segments)


def _window_candidates(day: date, start: time, end: time, zone: ZoneInfo):
    cursor, last = datetime.combine(day, start), datetime.combine(day, end)
    starts = set()
    while cursor < last:
        for fold in (0, 1):
            utc = cursor.replace(tzinfo=zone, fold=fold).astimezone(UTC)
            if utc.astimezone(zone).replace(tzinfo=None) == cursor:
                starts.add(utc.replace(tzinfo=None))
        cursor += timedelta(minutes=SLOT_STEP_MINUTES)
    return sorted(starts)


def _chain_parts(start: datetime, timings: Sequence[ChainTiming]):
    cursor = start
    parts = []
    for timing in timings:
        raw_end = cursor + timedelta(minutes=max(1, timing.duration_minutes))
        parts.append(
            (
                cursor,
                raw_end,
                cursor - timedelta(minutes=max(0, timing.buffer_before_minutes)),
                raw_end + timedelta(minutes=max(0, timing.buffer_after_minutes)),
            )
        )
        cursor = raw_end
    return parts


def chain_start_available(
    *,
    start: datetime,
    timings: Sequence[ChainTiming],
    timezone_name: str,
    schedules: ScheduleMap,
    exceptions: ExceptionMap,
    blocks: RangeMap,
    busy: RangeMap,
    staff_id: int,
    lead_time_minutes: int = 0,
    now: Optional[datetime] = None,
) -> bool:
    """Whether a whole chain can run back to back from ``start`` for one master.

    Every item must sit inside one working window of its local day, while its buffered
    range must stay clear of blocks and other visits. Items are not checked against each
    other: they are one booking, and their buffers describe the padding around it.
    """
    if not timings:
        return False
    current = now or utcnow()
    current = current.replace(tzinfo=UTC) if current.tzinfo is None else current.astimezone(UTC)
    lead_until = (current + timedelta(minutes=max(0, lead_time_minutes))).replace(tzinfo=None)
    if start < lead_until:
        return False

    zone = salon_zone(timezone_name)
    for raw_start, raw_end, occupied_start, occupied_end in _chain_parts(start, timings):
        local_start = raw_start.replace(tzinfo=UTC).astimezone(zone)
        if not any(
            left <= raw_start and raw_end <= right
            for window_start, window_end in _windows_for_day(
                staff_id, local_start.date(), schedules, exceptions
            )
            for left, right in _utc_window(local_start.date(), window_start, window_end, zone)
        ):
            return False
        if _overlaps(occupied_start, occupied_end, blocks.get(staff_id, ())):
            return False
        if _overlaps(occupied_start, occupied_end, busy.get(staff_id, ())):
            return False
    return True


def collect_chain_slots(
    *,
    date_from: date,
    date_to: date,
    timezone_name: str,
    horizon_days: int,
    lead_time_minutes: int,
    timings: Sequence[ChainTiming],
    master_ids: Sequence[int],
    schedules: ScheduleMap,
    exceptions: ExceptionMap,
    blocks: RangeMap,
    busy: RangeMap,
    now: Optional[datetime] = None,
    max_slots: int = MAX_SLOTS,
) -> list[AvailableSlot]:
    if not timings or not master_ids:
        return []
    zone = salon_zone(timezone_name)
    first, last, _today = booking_window(
        date_from, date_to, timezone_name, horizon_days, now=now
    )
    if last < first:
        return []

    current = now or utcnow()
    current = current.replace(tzinfo=UTC) if current.tzinfo is None else current.astimezone(UTC)
    lead_until = (current + timedelta(minutes=max(0, lead_time_minutes))).replace(tzinfo=None)
    collected: dict[date, dict[datetime, int]] = {}
    count = 0

    for offset in range((last - first).days + 1):
        day = first + timedelta(days=offset)
        for staff_id in master_ids:
            for window_start, window_end in _windows_for_day(
                staff_id, day, schedules, exceptions
            ):
                for start in _window_candidates(day, window_start, window_end, zone):
                    if start < lead_until:
                        continue
                    if chain_start_available(
                        start=start, timings=timings, timezone_name=timezone_name,
                        schedules=schedules, exceptions=exceptions, blocks=blocks, busy=busy,
                        staff_id=staff_id, lead_time_minutes=lead_time_minutes, now=current,
                    ):
                        bucket = collected.setdefault(day, {})
                        if start not in bucket:
                            bucket[start] = staff_id
                            count += 1
                            if count >= max_slots:
                                return [
                                    AvailableSlot(day=d, start=s, staff_id=i)
                                    for d in sorted(collected)
                                    for s, i in sorted(collected[d].items())
                                ]

    return [
        AvailableSlot(day=day, start=start, staff_id=staff_id)
        for day in sorted(collected)
        for start, staff_id in sorted(collected[day].items())
    ]
