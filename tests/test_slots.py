from datetime import datetime, timedelta, time
from app.api.core.slots import find_next_available_slot
def test_find_next_available_slot_no_conflicts():
    now = datetime.utcnow().replace(hour=9, minute=0, second=0, microsecond=0)
    existing = []
    slot = find_next_available_slot(existing, duration_minutes=60, start_from=now)
    assert slot == now
def test_find_next_available_slot_skips_conflict():
    start = datetime(2024, 1, 1, 9, 0)
    busy = [(start, start + timedelta(minutes=60))]
    slot = find_next_available_slot(busy, duration_minutes=60, start_from=start)
    assert slot == start + timedelta(minutes=60)
def test_find_next_available_slot_respects_break():
    start = datetime(2024, 1, 1, 9, 0)
    busy = []
    slot = find_next_available_slot(
        busy,
        duration_minutes=60,
        start_from=start,
        workday_start=start.time(),
        workday_end=datetime(2024, 1, 1, 12, 0).time(),
        breaks=[(time(10, 0), time(11, 0))],
    )
    assert slot == datetime(2024, 1, 1, 9, 0)
def test_find_next_available_slot_with_schedule_and_blocks():
    start = datetime(2024, 1, 1, 9, 0)  
    busy = []
    schedule = [
        (0, time(9, 0), time(12, 0)),  
    ]
    blocks = [
        (datetime(2024, 1, 1, 9, 0), datetime(2024, 1, 1, 10, 0)),
    ]
    slot = find_next_available_slot(
        busy,
        duration_minutes=60,
        start_from=start,
        schedule=schedule,
        blocks=blocks,
    )
    assert slot == datetime(2024, 1, 1, 10, 0)
