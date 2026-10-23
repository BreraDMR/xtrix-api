"""Multi-service visits: server totals, item layout, conflicts and public chains."""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.api.core.clock import utcnow
from app.db.models.visit import Visit
from app.db.models.visit_item import VisitItem

ZONE = ZoneInfo("Europe/Prague")


def _ok(response, what):
    assert response.status_code in (200, 201, 202), f"{what}: {response.status_code} {response.text}"
    return response.json()


@pytest.fixture
def salon(client, make_user, headers_for):
    owner = make_user("business_owner", full_name="Multi Owner", email="multi.owner@example.com")
    headers = headers_for(owner)
    business = _ok(
        client.post(
            "/businesses/",
            json={"name": "Multi Salon", "business_type": "salon", "timezone": "Europe/Prague"},
            headers=headers,
        ),
        "business",
    )
    public = _ok(
        client.put("/businesses/me/public-booking", json={"is_enabled": True}, headers=headers),
        "public settings",
    )
    client_row = _ok(
        client.post(
            "/clients/",
            json={"full_name": "Multi Client", "contact_phone": "+420700555111"},
            headers=headers,
        ),
        "client",
    )
    first = _ok(
        client.post(
            "/services/",
            json={
                "name": "Cut",
                "price": 20.10,
                "duration_minutes": 30,
                "buffer_before_minutes": 10,
                "specialist_id": owner.id,
            },
            headers=headers,
        ),
        "first service",
    )
    second = _ok(
        client.post(
            "/services/",
            json={
                "name": "Color",
                "price": 30.20,
                "duration_minutes": 45,
                "buffer_after_minutes": 5,
                "specialist_id": owner.id,
            },
            headers=headers,
        ),
        "second service",
    )
    employee = _ok(
        client.post(
            "/employees/create-with-user",
            json={
                "full_name": "Multi Employee",
                "email": "multi.employee@example.com",
                "phone": "+420700555222",
                "position": "colorist",
                "base_salary": 1000,
            },
            headers=headers,
        ),
        "employee",
    )
    employee_user_id = employee.get("employee_user_id") or employee.get("user_id")
    employee_service = _ok(
        client.post(
            "/services/",
            json={
                "name": "Employee Only",
                "price": 40,
                "duration_minutes": 30,
                "specialist_id": employee_user_id,
            },
            headers=headers,
        ),
        "employee service",
    )
    local_day = (utcnow() + timedelta(days=5)).astimezone(ZONE).date()
    _ok(
        client.post(
            "/schedules/work",
            json={
                "weekday": local_day.weekday(),
                "start_time": "09:00",
                "end_time": "17:00",
                "specialist_id": owner.id,
            },
            headers=headers,
        ),
        "work schedule",
    )
    return {
        "owner": owner,
        "headers": headers,
        "business": business,
        "slug": public["slug"],
        "client": client_row,
        "first": first,
        "second": second,
        "employee_user_id": employee_user_id,
        "employee_service": employee_service,
        "local_day": local_day,
        "start": datetime.combine(local_day, time(10, 0), tzinfo=ZONE)
        .astimezone(timezone.utc)
        .replace(tzinfo=None),
    }


def _chain_body(salon, start=None, **overrides):
    start = start or salon["start"]
    body = {
        "client_id": salon["client"]["id"],
        "specialist_id": salon["owner"].id,
        "service_id": salon["first"]["id"],
        "date_start": start.isoformat(),
        "price": 999,
        "items": [
            {"service_id": salon["first"]["id"]},
            {"service_id": salon["second"]["id"]},
        ],
    }
    body.update(overrides)
    return body


def test_chain_total_layout_and_snapshot_come_from_the_server(client, salon):
    visit = _ok(
        client.post("/visits/", json=_chain_body(salon), headers=salon["headers"]),
        "chain visit",
    )
    assert visit["service_id"] == salon["first"]["id"]
    assert visit["service_name"] == "Cut + Color"
    assert visit["price"] == pytest.approx(50.30)
    assert visit["date_start"] == salon["start"].isoformat() + "Z"
    assert visit["date_end"] == (salon["start"] + timedelta(minutes=75)).isoformat() + "Z"
    assert [item["position"] for item in visit["items"]] == [0, 1]
    assert [item["service_id"] for item in visit["items"]] == [
        salon["first"]["id"],
        salon["second"]["id"],
    ]
    assert visit["items"][0]["date_start"] == salon["start"].isoformat() + "Z"
    assert visit["items"][0]["date_end"] == (salon["start"] + timedelta(minutes=30)).isoformat() + "Z"
    assert visit["items"][1]["date_start"] == (salon["start"] + timedelta(minutes=30)).isoformat() + "Z"
    assert visit["items"][1]["date_end"] == (salon["start"] + timedelta(minutes=75)).isoformat() + "Z"
    assert sum(item["price"] for item in visit["items"]) == pytest.approx(visit["price"])


def test_legacy_single_visit_gets_one_item_and_keeps_working(client, salon):
    start = salon["start"] + timedelta(hours=2)
    legacy = _ok(
        client.post(
            "/visits/",
            json={
                "client_id": salon["client"]["id"],
                "specialist_id": salon["owner"].id,
                "service_id": salon["first"]["id"],
                "date_start": start.isoformat(),
                "price": 25,
            },
            headers=salon["headers"],
        ),
        "legacy visit",
    )
    assert legacy["price"] == 25
    assert len(legacy["items"]) == 1
    assert legacy["items"][0]["position"] == 0
    assert legacy["items"][0]["price"] == 25
    assert legacy["items"][0]["duration_minutes"] == 30

    moved = _ok(
        client.put(
            f"/visits/{legacy['id']}",
            json={"date_start": (start + timedelta(hours=1)).isoformat()},
            headers=salon["headers"],
        ),
        "move legacy",
    )
    assert moved["items"][0]["date_start"] == (start + timedelta(hours=1)).isoformat() + "Z"
    assert moved["date_end"] == (start + timedelta(hours=1, minutes=30)).isoformat() + "Z"

    changed = _ok(
        client.put(
            f"/visits/{legacy['id']}",
            json={"service_id": salon["second"]["id"]},
            headers=salon["headers"],
        ),
        "change service",
    )
    assert changed["items"][0]["service_id"] == salon["second"]["id"]
    assert changed["items"][0]["duration_minutes"] == 45
    assert changed["items"][0]["service_name"] == "Color"


def test_totals_use_decimal_cent_rounding(client, salon):
    odd = _ok(
        client.post(
            "/services/",
            json={
                "name": "Odd Price",
                "price": 10.005,
                "duration_minutes": 15,
                "specialist_id": salon["owner"].id,
            },
            headers=salon["headers"],
        ),
        "odd service",
    )
    visit = _ok(
        client.post(
            "/visits/",
            json=_chain_body(
                salon,
                start=salon["start"] + timedelta(hours=3),
                items=[
                    {"service_id": odd["id"]},
                    {"service_id": odd["id"]},
                ],
            ),
            headers=salon["headers"],
        ),
        "odd chain",
    )
    assert [item["price"] for item in visit["items"]] == [10.01, 10.01]
    assert visit["price"] == pytest.approx(20.02)


def test_items_can_be_replaced_in_place(client, salon):
    visit = _ok(
        client.post("/visits/", json=_chain_body(salon), headers=salon["headers"]),
        "chain visit",
    )
    replaced = _ok(
        client.put(
            f"/visits/{visit['id']}",
            json={
                "items": [
                    {"service_id": salon["second"]["id"]},
                    {"service_id": salon["first"]["id"]},
                ]
            },
            headers=salon["headers"],
        ),
        "replace items",
    )
    assert [item["service_id"] for item in replaced["items"]] == [
        salon["second"]["id"],
        salon["first"]["id"],
    ]
    assert replaced["items"][0]["duration_minutes"] == 45
    assert replaced["items"][1]["date_start"] == (
        salon["start"] + timedelta(minutes=45)
    ).isoformat() + "Z"
    assert replaced["price"] == pytest.approx(50.30)


def test_a_chain_cannot_change_one_service_without_items(client, salon):
    visit = _ok(
        client.post("/visits/", json=_chain_body(salon), headers=salon["headers"]),
        "chain visit",
    )
    response = client.put(
        f"/visits/{visit['id']}",
        json={"service_id": salon["second"]["id"]},
        headers=salon["headers"],
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["detail"]["code"] == "items_required"


def test_second_item_master_conflict_is_refused(client, salon):
    employee_id = salon["employee_user_id"]
    busy_start = salon["start"] + timedelta(hours=1)
    _ok(
        client.post(
            "/visits/",
            json={
                "client_id": salon["client"]["id"],
                "specialist_id": employee_id,
                "date_start": busy_start.isoformat(),
                "date_end": (busy_start + timedelta(hours=1)).isoformat(),
                "price": 10,
            },
            headers=salon["headers"],
        ),
        "employee visit",
    )

    clash = client.post(
        "/visits/",
        json=_chain_body(
            salon,
            start=busy_start,
            items=[
                {"service_id": salon["first"]["id"]},
                {"service_id": salon["second"]["id"], "specialist_id": employee_id},
            ],
        ),
        headers=salon["headers"],
    )
    assert clash.status_code == 409, clash.text

    free = client.post(
        "/visits/",
        json=_chain_body(
            salon,
            start=busy_start + timedelta(hours=1),
            items=[
                {"service_id": salon["first"]["id"]},
                {"service_id": salon["second"]["id"], "specialist_id": employee_id},
            ],
        ),
        headers=salon["headers"],
    )
    assert free.status_code == 200, free.text
    assert free.json()["items"][1]["specialist_id"] == employee_id


def test_failed_last_item_does_not_leave_the_first_one(client, db, salon):
    employee_id = salon["employee_user_id"]
    start = salon["start"] + timedelta(hours=2)
    _ok(
        client.post(
            "/visits/",
            json={
                "client_id": salon["client"]["id"],
                "specialist_id": employee_id,
                "date_start": start.isoformat(),
                "date_end": (start + timedelta(hours=1)).isoformat(),
                "price": 10,
            },
            headers=salon["headers"],
        ),
        "employee visit",
    )
    visits_before = db.query(Visit).count()
    items_before = db.query(VisitItem).count()

    response = client.post(
        "/visits/",
        json=_chain_body(
            salon,
            start=start,
            items=[
                {"service_id": salon["first"]["id"]},
                {"service_id": salon["second"]["id"], "specialist_id": employee_id},
            ],
        ),
        headers=salon["headers"],
    )
    assert response.status_code == 409, response.text
    assert db.query(Visit).count() == visits_before
    assert db.query(VisitItem).count() == items_before


def test_public_chain_slots_booking_replay_and_cancel(client, salon):
    staff_id = salon["owner"].id
    day = salon["local_day"].isoformat()
    slots_url = (
        f"/public/booking/{salon['slug']}/chain-slots"
        f"?service_ids={salon['first']['id']}&service_ids={salon['second']['id']}"
        f"&staff_id={staff_id}&date_from={day}&date_to={day}"
    )
    slots = _ok(client.get(slots_url), "chain slots")
    starts = [slot["start"] for slot in slots["days"][0]["slots"]]
    assert salon["start"].isoformat() + "Z" in starts
    assert (salon["start"] + timedelta(hours=1)).isoformat() + "Z" in starts

    payload = {
        "service_ids": [salon["first"]["id"], salon["second"]["id"]],
        "staff_id": staff_id,
        "start": salon["start"].isoformat() + "Z",
        "full_name": "Chain Guest",
        "email": "chain.guest@example.com",
        "processing_consent": True,
        "client_request_id": "chain-request-1",
    }
    booking = _ok(
        client.post(f"/public/booking/{salon['slug']}/bookings", json=payload),
        "public chain booking",
    )
    assert booking["price"] == pytest.approx(50.30)
    assert booking["service_name"] == "Cut + Color"
    assert booking["end"] == (salon["start"] + timedelta(minutes=75)).isoformat() + "Z"
    assert len(booking["items"]) == 2
    assert [item["service_id"] for item in booking["items"]] == [
        salon["first"]["id"],
        salon["second"]["id"],
    ]

    replay = _ok(
        client.post(f"/public/booking/{salon['slug']}/bookings", json=payload),
        "replay",
    )
    assert replay["id"] == booking["id"]

    conflict = client.post(
        f"/public/booking/{salon['slug']}/bookings",
        json={**payload, "client_request_id": "chain-request-2"},
    )
    assert conflict.status_code == 409, conflict.text

    slots_after = _ok(client.get(slots_url), "slots after booking")
    starts_after = [slot["start"] for slot in slots_after["days"][0]["slots"]]
    assert salon["start"].isoformat() + "Z" not in starts_after
    assert (salon["start"] - timedelta(hours=1)).isoformat() + "Z" not in starts_after
    assert (salon["start"] + timedelta(hours=1, minutes=30)).isoformat() + "Z" in starts_after

    for visit_id in (booking["id"],):
        response = client.put(
            f"/visits/{visit_id}", json={"status": "cancelled"}, headers=salon["headers"]
        )
        assert response.status_code == 200, response.text
    slots_released = _ok(client.get(slots_url), "slots after cancel")
    assert salon["start"].isoformat() + "Z" in [
        slot["start"] for slot in slots_released["days"][0]["slots"]
    ]


def test_public_chain_needs_a_master_for_every_service(client, salon):
    response = client.post(
        f"/public/booking/{salon['slug']}/bookings",
        json={
            "service_ids": [salon["first"]["id"], salon["employee_service"]["id"]],
            "staff_id": salon["owner"].id,
            "start": (salon["start"] + timedelta(hours=2)).isoformat() + "Z",
            "full_name": "Chain Guest",
            "email": "chain.guest@example.com",
            "processing_consent": True,
            "client_request_id": "chain-request-3",
        },
    )
    assert response.status_code == 404, response.text

    day = salon["local_day"].isoformat()
    url = (
        f"/public/booking/{salon['slug']}/chain-slots"
        f"?service_ids={salon['first']['id']}&service_ids={salon['employee_service']['id']}"
        f"&date_from={day}&date_to={day}"
    )
    slots = _ok(client.get(url), "intersection slots")
    assert slots["days"] == []


def test_revenue_reads_the_visit_total_once(client, salon):
    _ok(
        client.post(
            "/visits/",
            json=_chain_body(salon),
            headers=salon["headers"],
        ),
        "chain visit",
    )
    report = _ok(
        client.get(
            "/analytics/financial/report",
            params={
                "date_from": (salon["start"] - timedelta(hours=1)).isoformat() + "Z",
                "date_to": (salon["start"] + timedelta(days=1)).isoformat() + "Z",
            },
            headers=salon["headers"],
        ),
        "financial report",
    )
    assert report["total_revenue"] == pytest.approx(50.30)
    assert report["average_revenue_per_visit"] == pytest.approx(50.30)


@pytest.mark.parametrize("chain", [False, True])
def test_guest_move_updates_all_intervals_and_keeps_purchased_duration(client, db, salon, chain):
    from app.api.core.booking_manage_access import issue_token
    services = [salon["first"]["id"]] + ([salon["second"]["id"]] if chain else [])
    body = {
        "service_ids": services, "staff_id": salon["owner"].id,
        "start": salon["start"].isoformat() + "Z", "full_name": "Move Guest",
        "email": "move.guest@example.com", "processing_consent": True,
        "client_request_id": "before-guest-move",
    }
    url = f"/public/booking/{salon['slug']}/bookings"
    booking = _ok(client.post(url, json=body), "booking")
    visit = db.get(Visit, booking["id"])
    raw, _ = issue_token(db, visit)
    # Editing the catalog must not change the duration already booked.
    _ok(client.put(f"/services/{services[0]}", json={"duration_minutes": 15},
                   headers=salon["headers"]), "change catalog duration")
    target = salon["start"] + timedelta(hours=3)
    moved = _ok(client.post("/public/booking-management/reschedule",
                           headers={"X-Booking-Token": raw}, json={"start": target.isoformat() + "Z"}), "move")
    duration = 75 if chain else 30
    assert moved["end"] == (target + timedelta(minutes=duration)).isoformat() + "Z"
    db.expire_all()
    visit = db.get(Visit, booking["id"])
    assert visit.items[0].date_start == target
    assert visit.items[-1].date_end == target + timedelta(minutes=duration)
    # Another guest can take the released time, but never the new time.
    assert client.post(url, json={**body, "client_request_id": "released-slot"}).status_code == 200
    assert client.post(url, json={**body, "start": target.isoformat() + "Z",
                                  "client_request_id": "occupied-slot"}).status_code == 409
    events_before = client.post("/public/booking-management/reschedule",
                               headers={"X-Booking-Token": raw}, json={"start": target.isoformat() + "Z"})
    assert events_before.status_code == 200


def test_guest_move_checks_the_last_procedure_and_preserves_old_slot_on_conflict(client, db, salon):
    from app.api.core.booking_manage_access import issue_token
    visit = _ok(client.post("/visits/", json=_chain_body(salon), headers=salon["headers"]), "chain")
    raw, _ = issue_token(db, db.get(Visit, visit["id"]))
    target = salon["start"] + timedelta(hours=3)
    _ok(client.post("/visits/", headers=salon["headers"], json={
        "client_id": salon["client"]["id"], "specialist_id": salon["owner"].id,
        "date_start": (target + timedelta(minutes=45)).isoformat(),
        "date_end": (target + timedelta(minutes=75)).isoformat(), "price": 10,
    }), "busy tail")
    result = client.post("/public/booking-management/reschedule", headers={"X-Booking-Token": raw},
                         json={"start": target.isoformat() + "Z"})
    assert result.status_code == 409, result.text
    db.expire_all()
    stored = db.get(Visit, visit["id"])
    assert stored.date_start == salon["start"]
    assert stored.items[0].date_start == salon["start"]
