from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from tests.tenant_world import build_tenant


def _slot(days=6, hour=14):
    start = (datetime.utcnow() + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)
    return start, start + timedelta(hours=1)


def _body(a, start, end):
    return {"client_id": a["client"]["id"], "specialist_id": a["owner"].id,
            "date_start": start.isoformat(), "date_end": end.isoformat(), "price": 1}


def test_parallel_bookings_of_one_slot_let_exactly_one_through(client, make_user, headers_for):
    # the free-slot check is a plain SELECT, so requests racing each other all used to pass it
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    start, end = _slot()
    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(lambda _: client.post("/visits/", json=_body(a, start, end), headers=a["headers"]).status_code, range(8)))
    assert codes.count(200) == 1, codes
    assert set(codes) == {200, 409}, codes


def test_the_database_refuses_an_overlapping_visit(db, client, make_user, headers_for):
    # whatever the API does, two active visits of one specialist can't overlap in the table
    from app.db.models.visit import Visit

    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    v = a["visit"]
    start = datetime.fromisoformat(v["date_start"].rstrip("Z")) + timedelta(minutes=30)
    db.add(Visit(client_id=a["client"]["id"], specialist_id=a["owner"].id, date_start=start, status="planned"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_a_visit_without_an_end_holds_an_hour(db, client, make_user, headers_for):
    from app.db.models.visit import Visit

    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    start, _ = _slot(days=8)
    db.add(Visit(client_id=a["client"]["id"], specialist_id=a["owner"].id, date_start=start, status="planned"))
    db.commit()
    db.add(Visit(client_id=a["client"]["id"], specialist_id=a["owner"].id, date_start=start + timedelta(minutes=59), status="planned"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    # right after the hour is fine
    db.add(Visit(client_id=a["client"]["id"], specialist_id=a["owner"].id, date_start=start + timedelta(hours=1), status="planned"))
    db.commit()


def test_cancelled_visit_cannot_come_back_onto_a_taken_slot(client, make_user, headers_for):
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    start, end = _slot()
    first = client.post("/visits/", json=_body(a, start, end), headers=a["headers"]).json()
    assert client.put(f"/visits/{first['id']}", json={"status": "cancelled"}, headers=a["headers"]).status_code == 200
    # the cancelled visit doesn't hold the slot
    second = client.post("/visits/", json=_body(a, start, end), headers=a["headers"])
    assert second.status_code == 200, second.text

    r = client.put(f"/visits/{first['id']}", json={"status": "planned"}, headers=a["headers"])
    assert r.status_code == 409, r.text
    assert "already" in r.json()["error"]["message"]


def test_restoring_a_deleted_visit_onto_a_taken_slot_is_refused(client, make_user, headers_for):
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    start, end = _slot()
    first = client.post("/visits/", json=_body(a, start, end), headers=a["headers"]).json()
    assert client.delete(f"/visits/{first['id']}", headers=a["headers"]).status_code == 200
    assert client.post("/visits/", json=_body(a, start, end), headers=a["headers"]).status_code == 200

    r = client.post(f"/visits/{first['id']}/restore", headers=a["headers"])
    assert r.status_code == 409, r.text
    assert "already" in r.json()["error"]["message"]
