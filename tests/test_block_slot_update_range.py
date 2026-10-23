"""A partial update validates the merged interval before changing a saved block."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.core.clock import utcnow
from app.db.models.block_slot import BlockSlot
from app.main import app


@pytest.mark.parametrize("change", ["end_before", "start_after", "too_short", "too_long", "null_start", "null_end"])
def test_invalid_partial_block_update_keeps_the_original_interval(db, make_user, headers_for, change):
    client = TestClient(app, raise_server_exceptions=False)
    user = make_user("specialist")
    headers = headers_for(user)
    start = (utcnow() + timedelta(days=3)).replace(microsecond=0)
    end = start + timedelta(hours=1)
    created = client.post("/schedules/blocks", headers=headers, json={
        "specialist_id": user.id, "start": start.isoformat() + "Z", "end": end.isoformat() + "Z"})
    assert created.status_code == 200, created.text
    payloads = {
        "end_before": {"end": (start - timedelta(minutes=1)).isoformat() + "Z"},
        "start_after": {"start": (end + timedelta(minutes=1)).isoformat() + "Z"},
        "too_short": {"end": (start + timedelta(minutes=10)).isoformat() + "Z"},
        "too_long": {"end": (start + timedelta(hours=25)).isoformat() + "Z"},
        "null_start": {"start": None}, "null_end": {"end": None},
    }
    result = client.put(f"/schedules/blocks/{created.json()['id']}", headers=headers, json=payloads[change])
    assert result.status_code == 422, result.text
    db.expire_all()
    row = db.get(BlockSlot, created.json()["id"])
    assert row.start == start and row.end == end


def test_a_valid_end_only_update_preserves_the_start(client, make_user, headers_for):
    user = make_user("specialist")
    headers = headers_for(user)
    start = (utcnow() + timedelta(days=3)).replace(microsecond=0)
    created = client.post("/schedules/blocks", headers=headers, json={"specialist_id": user.id,
        "start": start.isoformat() + "Z", "end": (start + timedelta(hours=1)).isoformat() + "Z"})
    assert created.status_code == 200, created.text
    result = client.put(f"/schedules/blocks/{created.json()['id']}", headers=headers,
        json={"end": (start + timedelta(hours=2)).isoformat() + "Z"})
    assert result.status_code == 200, result.text
    assert result.json()["start"] == created.json()["start"]
