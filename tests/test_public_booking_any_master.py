"""Public booking: service categories and the "any master" choice for one service."""
from concurrent.futures import ThreadPoolExecutor

from tests.test_public_booking_acceptance import _body, _book, _local_utc, _ok, _salon, _visits


def _two_masters(client, make_user, headers_for, name):
    salon = _salon(client, make_user, headers_for, name)
    for weekday in range(7):
        _ok(client.post("/schedules/work", json={
            "weekday": weekday, "start_time": "09:00", "end_time": "17:00", "specialist_id": salon["employee_id"],
        }, headers=salon["headers"]), "employee work")
    assert salon["owner"].id < salon["employee_id"]
    return salon


def _any(salon, start, key, **extra):
    body = _body(salon, start, key, **extra)
    body.pop("staff_id")
    return body


def _slot_staff(client, salon, day, start):
    slots = _ok(client.get(f"/public/booking/{salon['slug']}/slots", params={
        "service_id": salon["service"]["id"], "date_from": day.isoformat(),
    }), "any slots")
    matches = [slot for d in slots["days"] for slot in d["slots"] if slot["start"] == start]
    assert len(matches) <= 1, matches
    return matches[0]["staff_id"] if matches else None


def test_services_carry_their_category(client, make_user, headers_for):
    salon = _salon(client, make_user, headers_for, "Groups")
    colour = _ok(client.post("/services/", json={
        "name": "Colour", "duration_minutes": 90, "price": 900, "category": "Hair colour",
        "specialist_id": salon["owner"].id,
    }, headers=salon["headers"]), "colour")
    services = {s["id"]: s for s in _ok(client.get(f"/public/booking/{salon['slug']}/services"), "services")}
    assert services[colour["id"]]["category"] == "Hair colour"
    assert set(services[colour["id"]]) == {"id", "name", "category", "description", "duration_minutes", "price"}


def test_any_master_goes_to_the_least_loaded_then_lowest_id(client, make_user, headers_for, db):
    salon = _two_masters(client, make_user, headers_for, "Anyone")
    owner, employee = salon["owner"].id, salon["employee_id"]
    noon, day = _local_utc(hour=12)
    two, _ = _local_utc(hour=14)
    ten, _ = _local_utc(hour=10)

    # nobody booked yet: a tie, so the lower id
    assert _slot_staff(client, salon, day, noon) == owner
    first = _ok(_book(client, salon, _any(salon, noon, "any-first-1")), "first any")
    assert first["staff_id"] == owner
    # the owner has an hour that day now, the employee none
    assert _slot_staff(client, salon, day, two) == employee
    second = _ok(_book(client, salon, _any(salon, two, "any-second-1", email="second@example.com")), "second any")
    assert second["staff_id"] == employee
    # one hour each again, back to the lower id
    third = _ok(_book(client, salon, _any(salon, ten, "any-third-1", email="third@example.com")), "third any")
    assert third["staff_id"] == owner
    assert sorted(v.specialist_id for v in _visits(db, salon["business"]["id"])) == [owner, owner, employee]


def test_any_master_picks_again_when_the_shown_master_was_taken(client, make_user, headers_for, db):
    salon = _two_masters(client, make_user, headers_for, "Retake")
    start, day = _local_utc(hour=15)
    assert _slot_staff(client, salon, day, start) == salon["owner"].id
    # someone books the owner by name between the guest's look and their tap
    _ok(_book(client, salon, _body(salon, start, "named-owner-1", email="named@example.com")), "named")
    booked = _ok(_book(client, salon, _any(salon, start, "any-retake-1")), "any after named")
    assert booked["staff_id"] == salon["employee_id"]
    # both busy at that time now
    assert _slot_staff(client, salon, day, start) is None
    full = _book(client, salon, _any(salon, start, "any-retake-2", email="late@example.com"))
    assert full.status_code == 409, full.text
    assert len(_visits(db, salon["business"]["id"])) == 2


def test_parallel_any_master_bookings_never_double_book(client, make_user, headers_for, db):
    salon = _two_masters(client, make_user, headers_for, "Rush")
    start, _day = _local_utc(hour=11)

    def attempt(n):
        return _book(client, salon, _any(salon, start, f"any-rush-{n:02d}", email=f"rush{n}@example.com")).status_code

    with ThreadPoolExecutor(max_workers=5) as pool:
        codes = list(pool.map(attempt, range(5)))
    assert codes.count(200) == 2, codes
    assert set(codes) == {200, 409}, codes
    visits = _visits(db, salon["business"]["id"])
    assert sorted(v.specialist_id for v in visits) == sorted([salon["owner"].id, salon["employee_id"]])


def test_any_master_keeps_to_masters_of_the_service(client, make_user, headers_for, db):
    salon = _two_masters(client, make_user, headers_for, "Solo")
    only_owner = _ok(client.post("/services/", json={
        "name": "Owner only", "duration_minutes": 60, "price": 100, "specialist_id": salon["owner"].id,
        "specialist_ids": [salon["owner"].id],
    }, headers=salon["headers"]), "owner only")
    start, _day = _local_utc(hour=13)
    _ok(_book(client, salon, _body(salon, start, "solo-named-1", service_id=only_owner["id"])), "owner booked")
    # the employee is free then, but doesn't do this service
    other = _book(client, salon, _any(salon, start, "solo-any-1", service_id=only_owner["id"], email="x@example.com"))
    assert other.status_code == 409, other.text


def test_a_chain_still_needs_a_named_master(client, make_user, headers_for):
    salon = _two_masters(client, make_user, headers_for, "Chain")
    start, _day = _local_utc(hour=9)
    body = _any(salon, start, "chain-any-1")
    body.pop("service_id")
    body["service_ids"] = [salon["service"]["id"], salon["service"]["id"]]
    response = _book(client, salon, body)
    assert response.status_code == 422, response.text
    assert "staff_required" in response.text
