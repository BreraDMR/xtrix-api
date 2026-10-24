"""Busy slots must advance even when unrelated breaks or blocks exist."""
import subprocess
import sys

import pytest


@pytest.mark.parametrize('extra', [
    'breaks=[(time(12),time(13))]',
    'blocks=[(datetime(2030,5,7,12),datetime(2030,5,7,13))]',
])
def test_search_advances_past_busy_slot_with_unrelated_calendar_entries(extra):
    source = f'''from datetime import datetime,time
from app.api.core.slots import find_next_available_slot
print(find_next_available_slot([(datetime(2030,5,6,9),datetime(2030,5,6,10))],30,
    start_from=datetime(2030,5,6,9),days_ahead=1,{extra}).isoformat())
'''
    result = subprocess.run([sys.executable, '-c', source], capture_output=True, text=True, timeout=5, check=True)
    assert result.stdout.strip() == '2030-05-06T10:00:00'
