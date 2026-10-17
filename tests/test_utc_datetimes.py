import json
from datetime import datetime, timedelta
from pathlib import Path

from tests.tenant_world import build_tenant

# The db keeps naive UTC. Responses used to send it as is ("2026-11-02T09:00:00"), so a browser
# read it as local time and showed visits 1-2 hours off. Now every datetime goes out as UTC with Z,
# a timezone sent with the request is respected, and a naive one still means UTC as before.


def _day():
    return (datetime.utcnow() + timedelta(days=9)).date().isoformat()


def _visit(client, a, start, end):
    r = client.post("/visits/", headers=a["headers"], json={
        "client_id": a["client"]["id"], "specialist_id": a["owner"].id, "date_start": start, "date_end": end, "price": 1,
    })
    assert r.status_code == 200, r.text
    return r.json()


def test_visit_times_go_out_as_utc_with_z(client, make_user, headers_for):
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    d = _day()
    v = _visit(client, a, f"{d}T09:00:00Z", f"{d}T10:00:00Z")
    assert v["date_start"] == f"{d}T09:00:00Z"
    assert v["date_end"] == f"{d}T10:00:00Z"
    assert v["created_at"].endswith("Z")
    assert client.get(f"/visits/{v['id']}", headers=a["headers"]).json()["date_start"] == f"{d}T09:00:00Z"


def test_an_offset_in_the_request_is_converted_not_dropped(client, make_user, headers_for, db):
    from app.db.models.visit import Visit

    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    d = _day()
    v = _visit(client, a, f"{d}T14:00:00+02:00", f"{d}T15:00:00+02:00")
    assert v["date_start"] == f"{d}T12:00:00Z"
    stored = db.get(Visit, v["id"])
    assert stored.date_start == datetime.fromisoformat(f"{d}T12:00:00")


def test_a_naive_time_still_means_utc(client, make_user, headers_for):
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    d = _day()
    v = _visit(client, a, f"{d}T16:00:00", f"{d}T17:00:00")
    assert v["date_start"] == f"{d}T16:00:00Z"


def test_other_records_carry_z_too(client, make_user, headers_for):
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    assert a["client"]["created_at"].endswith("Z")
    assert a["business"]["created_at"].endswith("Z")
    assert client.get("/user-preferences/me", headers=a["headers"]).json()["created_at"].endswith("Z")


def test_qr_history_dates_carry_z(client, make_user, headers_for):
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    qr = client.post(f"/clients/{a['client']['id']}/qr-code/generate", headers=a["headers"]).json()
    r = client.post("/clients/qr-code/access", json={"qr_code_data": qr["qr_code_data"], "password": qr["access_password"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["last_visit_date"].endswith("Z")
    assert all(v["date_start"].endswith("Z") for v in body["visits"])


def test_contract_still_says_date_time():
    spec = json.loads((Path(__file__).resolve().parents[1] / "openapi.json").read_text())
    prop = spec["components"]["schemas"]["VisitOut"]["properties"]["date_start"]
    assert prop.get("format") == "date-time", prop
    # no "-Input"/"-Output" twins: the frontend's generated type names must stay as they are
    assert not any(name.endswith(("-Input", "-Output")) for name in spec["components"]["schemas"])
