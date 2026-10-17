from datetime import datetime, timedelta, time
from typing import List, Tuple, Optional
from app.api.core.clock import utcnow
def _overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return max(a_start, b_start) < min(a_end, b_end)
def find_next_available_slot(
    existing: List[Tuple[datetime, datetime]],
    duration_minutes: int,
    start_from: datetime | None = None,
    days_ahead: int = 7,
    workday_start: time = time(9, 0),
    workday_end: time = time(18, 0),
    step_minutes: int = 15,
    breaks: Optional[List[Tuple[time, time]]] = None,
    schedule: Optional[List[Tuple[int, time, time]]] = None,  
    blocks: Optional[List[Tuple[datetime, datetime]]] = None,
) -> datetime | None:
    start_search = start_from or utcnow()
    delta = timedelta(minutes=step_minutes)
    duration = timedelta(minutes=duration_minutes)
    for day_offset in range(days_ahead):
        day = (start_search + timedelta(days=day_offset)).date()
        if schedule:
            day_windows = [s for s in schedule if s[0] == day.weekday()]
            if not day_windows:
                continue
        else:
            day_windows = [(day.weekday(), workday_start, workday_end)]
        for _, ws_start, ws_end in day_windows:
            slot = datetime.combine(day, ws_start)
            end_of_day = datetime.combine(day, ws_end)
            if slot < start_search:
                slot = start_search.replace(second=0, microsecond=0)
            while slot + duration <= end_of_day:
                conflict = any(_overlaps(slot, slot + duration, b[0], b[1]) for b in existing)
                if breaks:
                    for br_start, br_end in breaks:
                        br_s = datetime.combine(day, br_start)
                        br_e = datetime.combine(day, br_end)
                        if _overlaps(slot, slot + duration, br_s, br_e):
                            conflict = True
                            slot = br_e
                            break
                    if conflict:
                        continue
                if blocks:
                    for blk_start, blk_end in blocks:
                        if _overlaps(slot, slot + duration, blk_start, blk_end):
                            conflict = True
                            slot = blk_end
                            break
                    if conflict:
                        continue
                if not conflict:
                    return slot
                slot += delta
    return None
