"""BK-21 acceptance: the public booking link, checked end to end over HTTP.

One test per "done" line of BK-01..12, so a failure points straight at the card it breaks.
Everything runs on synthetic salons with example.com contacts; nothing is sent anywhere.
"""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.db.models.client import Client
from app.db.models.client_business_relation import ClientBusinessRelation
from app.db.models.public_booking import PublicBookingRequest
from app.db.models.visit import Visit
from tests.test_rate_limit import _run

PRAGUE = ZoneInfo("Europe/Prague")
STAFF_PHONE = "+420777999{:03d}"
CARD_KEYS = {
    "slug", "name", "business_type", "address", "phone", "email", "description",
    "currency", "timezone", "deposit_type", "deposit_value", "deposit_hold_minutes",
}


def _ok(response, what):
    assert response.status_code == 200, f"{what}: {response.status_code} {response.text[:400]}"
    return response.json()


def _salon(client, make_user, headers_for, name, *, enable=True, staff_email=None):
    """A salon with one 60-minute service, the owner working 09-17 every day, and one employee."""
    staff_phone = STAFF_PHONE.format(sum(map(ord, name)) % 1000)
    owner = make_user("business_owner", full_name=f"{name} Owner", email=f"{name.lower()}.owner@example.com")
    headers = headers_for(owner)
    business = _ok(client.post("/businesses/", json={
        "name": f"{name} Salon", "business_type": "salon", "timezone": "Europe/Prague",
        "phone": "+420777000111", "email": f"{name.lower()}@example.com", "address": "Main 1",
    }, headers=headers), "business")
    employee = _ok(client.post("/employees/create-with-user", json={
        "full_name": f"{name} Master", "email": staff_email or f"{name.lower()}.master@example.com",
        "phone": staff_phone, "position": "Stylist", "base_salary": 1000,
    }, headers=headers), "employee")
    employee = employee.get("employee", employee)
    employee_id = employee.get("employee_user_id") or employee.get("user_id")
    service = _ok(client.post("/services/", json={
        "name": f"{name} Cut", "duration_minutes": 60, "price": 450,
        "specialist_id": owner.id, "specialist_ids": [owner.id, employee_id],
    }, headers=headers), "service")
    for weekday in range(7):
        _ok(client.post("/schedules/work", json={
            "weekday": weekday, "start_time": "09:00", "end_time": "17:00", "specialist_id": owner.id,
        }, headers=headers), "work")
    settings = _ok(client.get("/businesses/me/public-booking", headers=headers), "settings")
    if enable:
        settings = _ok(client.put("/businesses/me/public-booking", json={"is_enabled": True}, headers=headers), "enable")
    return {
        "owner": owner, "headers": headers, "business": business, "service": service,
        "employee_id": employee_id, "staff_phone": staff_phone, "slug": settings["slug"], "settings": settings,
    }


def _local_utc(days_ahead=5, hour=10):
    """An ISO UTC instant for hour:00 Prague time a few days from now."""
    day = (datetime.now(PRAGUE) + timedelta(days=days_ahead)).date()
    local = datetime.combine(day, time(hour), tzinfo=PRAGUE)
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), day


def _body(salon, start, key, **extra):
    body = {
        "service_id": salon["service"]["id"], "staff_id": salon["owner"].id, "start": start,
        "full_name": "Guest Example", "email": "guest@example.com",
        "processing_consent": True, "client_request_id": key,
    }
    body.update(extra)
    return body


def _book(client, salon, body):
    return client.post(f"/public/booking/{salon['slug']}/bookings", json=body)


def _visits(db, business_id):
    db.expire_all()
    return db.query(Visit).filter(Visit.business_id == business_id, Visit.deleted_at.is_(None)).all()


# BK-01/02: off by default, a slug clash is an error and never takes the neighbour's link

def test_link_is_off_until_the_owner_publishes_it(client, make_user, headers_for):
    salon = _salon(client, make_user, headers_for, "Quiet", enable=False)
    assert salon["settings"]["is_enabled"] is False
    assert client.get(f"/public/booking/{salon['slug']}").status_code == 404
    assert client.get(f"/public/booking/{salon['slug']}/services").status_code == 404


def test_slug_collision_is_409_and_leaves_the_first_salon_alone(client, make_user, headers_for):
    first = _salon(client, make_user, headers_for, "First")
    second = _salon(client, make_user, headers_for, "Second")
    clash = client.put("/businesses/me/public-booking", json={"slug": first["slug"]}, headers=second["headers"])
    assert clash.status_code == 409, clash.text
    card = _ok(client.get(f"/public/booking/{first['slug']}"), "first card")
    assert card["name"] == "First Salon"
    assert _ok(client.get("/businesses/me/public-booking", headers=second["headers"]), "second")["slug"] == second["slug"]


def test_staff_cannot_publish_or_rename_the_link(client, make_user, headers_for, db):
    from app.db.models.user import User

    salon = _salon(client, make_user, headers_for, "Owned")
    employee = db.query(User).filter(User.id == salon["employee_id"]).one()
    response = client.put("/businesses/me/public-booking", json={"is_enabled": False}, headers=headers_for(employee))
    assert response.status_code == 403, response.text
    assert client.get(f"/public/booking/{salon['slug']}").status_code == 200


# BK-03: unknown and switched-off links look identical, the card carries no internals

def test_unknown_and_disabled_links_give_the_same_answer(client, make_user, headers_for):
    salon = _salon(client, make_user, headers_for, "Hidden")
    _ok(client.put("/businesses/me/public-booking", json={"is_enabled": False}, headers=salon["headers"]), "disable")
    disabled = client.get(f"/public/booking/{salon['slug']}")
    unknown = client.get("/public/booking/no-such-salon-here")
    assert disabled.status_code == unknown.status_code == 404
    assert disabled.json()["error"]["message"] == unknown.json()["error"]["message"]
    for path in ("services", "staff?service_id=1", "slots?service_id=1&date_from=2030-01-01"):
        assert client.get(f"/public/booking/{salon['slug']}/{path}").status_code == 404
    start, _day = _local_utc()
    assert _book(client, salon, _body(salon, start, "disabled-post-1")).status_code == 404


def test_public_card_is_an_allowlist(client, make_user, headers_for):
    salon = _salon(client, make_user, headers_for, "Card")
    card = _ok(client.get(f"/public/booking/{salon['slug']}"), "card")
    assert set(card) == CARD_KEYS
    text = json.dumps(card)
    assert "card.owner@example.com" not in text and "card.master@example.com" not in text
    assert salon["staff_phone"] not in text


# BK-04/05: only bookable services, only their masters, no contacts or internal fields

def test_services_list_only_active_timed_services_with_public_fields(client, make_user, headers_for):
    salon = _salon(client, make_user, headers_for, "Menu")
    hidden = _ok(client.post("/services/", json={
        "name": "Paused", "duration_minutes": 30, "price": 1, "specialist_id": salon["owner"].id,
    }, headers=salon["headers"]), "paused")
    _ok(client.put(f"/services/{hidden['id']}", json={"is_active": False}, headers=salon["headers"]), "pause")
    _ok(client.post("/services/", json={
        "name": "Untimed", "price": 1, "specialist_id": salon["owner"].id,
    }, headers=salon["headers"]), "untimed")
    other = _salon(client, make_user, headers_for, "Rival")

    services = _ok(client.get(f"/public/booking/{salon['slug']}/services"), "services")
    assert [s["id"] for s in services] == [salon["service"]["id"]]
    assert set(services[0]) == {"id", "name", "description", "duration_minutes", "price"}
    assert services[0]["duration_minutes"] == 60 and services[0]["price"] == 450
    # a neighbour's service id is simply not there under this slug
    assert client.get(f"/public/booking/{salon['slug']}/staff", params={"service_id": other["service"]["id"]}).status_code == 404
    assert client.get(f"/public/booking/{salon['slug']}/staff", params={"service_id": hidden["id"]}).status_code == 404


def test_staff_list_is_service_masters_without_contacts(client, make_user, headers_for):
    salon = _salon(client, make_user, headers_for, "Team")
    staff = _ok(client.get(f"/public/booking/{salon['slug']}/staff", params={"service_id": salon["service"]["id"]}), "staff")
    assert {person["id"] for person in staff} == {salon["owner"].id, salon["employee_id"]}
    for person in staff:
        assert set(person) == {"id", "full_name", "position"}
    text = json.dumps(staff)
    assert "@example.com" not in text and salon["staff_phone"] not in text


def test_foreign_master_is_not_bookable_here(client, make_user, headers_for, db):
    salon = _salon(client, make_user, headers_for, "Home")
    other = _salon(client, make_user, headers_for, "Away")
    start, day = _local_utc()
    slots = client.get(f"/public/booking/{salon['slug']}/slots", params={
        "service_id": salon["service"]["id"], "staff_id": other["owner"].id, "date_from": day.isoformat(),
    })
    assert slots.status_code == 404, slots.text
    booked = _book(client, salon, _body(salon, start, "foreign-master-1", staff_id=other["owner"].id))
    assert booked.status_code == 404, booked.text
    assert _visits(db, salon["business"]["id"]) == []


# BK-06/07: real occupancy and range caps

def test_slots_skip_existing_visits_and_cap_the_range(client, make_user, headers_for):
    salon = _salon(client, make_user, headers_for, "Busy")
    start, day = _local_utc()
    params = {"service_id": salon["service"]["id"], "staff_id": salon["owner"].id, "date_from": day.isoformat()}
    before = _ok(client.get(f"/public/booking/{salon['slug']}/slots", params=params), "slots")
    starts = {slot["start"] for d in before["days"] for slot in d["slots"]}
    assert start in starts
    assert before["timezone"] == "Europe/Prague"
    _ok(_book(client, salon, _body(salon, start, "busy-first-1")), "first booking")
    after = _ok(client.get(f"/public/booking/{salon['slug']}/slots", params=params), "slots again")
    assert start not in {slot["start"] for d in after["days"] for slot in d["slots"]}

    too_long = dict(params, date_to=(day + timedelta(days=14)).isoformat())
    assert client.get(f"/public/booking/{salon['slug']}/slots", params=too_long).status_code == 422
    backwards = dict(params, date_to=(day - timedelta(days=1)).isoformat())
    assert client.get(f"/public/booking/{salon['slug']}/slots", params=backwards).status_code == 422
    widest = dict(params, date_to=(day + timedelta(days=13)).isoformat())
    assert client.get(f"/public/booking/{salon['slug']}/slots", params=widest).status_code == 200


# BK-08: the guest can't set price, tenant or status; bad input is 422

def test_guest_cannot_smuggle_server_fields(client, make_user, headers_for):
    salon = _salon(client, make_user, headers_for, "Strict")
    start, _day = _local_utc()
    for field, value in (("price", 1), ("business_id", 1), ("status", "completed"), ("payment_status", "paid")):
        response = _book(client, salon, _body(salon, start, f"smuggle-{field}"[:64].replace("_", "-"), **{field: value}))
        assert response.status_code == 422, (field, response.text)


def test_contact_name_and_consent_are_validated(client, make_user, headers_for):
    salon = _salon(client, make_user, headers_for, "Valid")
    start, _day = _local_utc()
    cases = {
        "both contacts": _body(salon, start, "both-contacts", phone="+420777123456"),
        "no contact": {k: v for k, v in _body(salon, start, "no-contact-1").items() if k != "email"},
        "blank name": _body(salon, start, "blank-name-1", full_name="   "),
        "name with newline": _body(salon, start, "newline-name", full_name="Ann\nBcc: x"),
        "no consent": _body(salon, start, "no-consent-1", processing_consent=False),
        "string consent": _body(salon, start, "str-consent-1", processing_consent="yes"),
        "bad email": _body(salon, start, "bad-email-01", email="not-an-email"),
        "short key": _body(salon, start, "short"),
        "odd key": _body(salon, start, "bad key with spaces"),
    }
    for label, body in cases.items():
        response = _book(client, salon, body)
        assert response.status_code == 422, (label, response.status_code, response.text[:200])


def test_marketing_consent_is_off_unless_ticked(client, make_user, headers_for, db):
    salon = _salon(client, make_user, headers_for, "Consent")
    start, _day = _local_utc()
    _ok(_book(client, salon, _body(salon, start, "consent-default")), "booking")
    claim = db.query(PublicBookingRequest).filter(PublicBookingRequest.request_id == "consent-default").one()
    assert claim.marketing_consent is False and claim.processing_consent is True


def test_price_and_status_come_from_the_server(client, make_user, headers_for, db):
    salon = _salon(client, make_user, headers_for, "Priced")
    start, _day = _local_utc()
    booked = _ok(_book(client, salon, _body(salon, start, "priced-booking")), "booking")
    assert booked["price"] == 450 and booked["status"] == "planned"
    assert booked["manage_token"]
    visit = db.query(Visit).filter(Visit.id == booked["id"]).one()
    assert visit.business_id == salon["business"]["id"] and float(visit.price) == 450


# BK-09: contact matching stays inside the salon

def test_a_contact_known_in_another_salon_is_not_linked(client, make_user, headers_for, db):
    home = _salon(client, make_user, headers_for, "Mine")
    other = _salon(client, make_user, headers_for, "Theirs")
    stranger = _ok(client.post("/clients/", json={
        "full_name": "Secret Stranger", "contact_email": "shared@example.com",
    }, headers=other["headers"]), "foreign client")
    start, _day = _local_utc()
    booked = _ok(_book(client, home, _body(home, start, "shared-contact", email="shared@example.com", full_name="New Guest")), "booking")
    assert "Secret" not in json.dumps(booked)
    visit = db.query(Visit).filter(Visit.id == booked["id"]).one()
    assert visit.client_id != stranger["id"]
    relations = {row.business_id for row in db.query(ClientBusinessRelation).filter(ClientBusinessRelation.client_id == stranger["id"])}
    assert relations == {other["business"]["id"]}
    assert db.query(Client).filter(Client.id == visit.client_id).one().full_name == "New Guest"


def test_a_returning_guest_reuses_their_card_in_the_same_salon(client, make_user, headers_for, db):
    salon = _salon(client, make_user, headers_for, "Return")
    known = _ok(client.post("/clients/", json={
        "full_name": "Known Guest", "contact_phone": "+420 777 222 333",
    }, headers=salon["headers"]), "client")
    start, _day = _local_utc()
    booked = _ok(_book(client, salon, {
        **{k: v for k, v in _body(salon, start, "returning-guest").items() if k != "email"},
        "phone": "+420777222333",
    }), "booking")
    assert db.query(Visit).filter(Visit.id == booked["id"]).one().client_id == known["id"]


# BK-10: two guests racing for one slot get one visit

def test_parallel_guests_for_one_slot_get_one_visit(client, make_user, headers_for, db):
    salon = _salon(client, make_user, headers_for, "Race")
    start, _day = _local_utc()

    def attempt(n):
        return _book(client, salon, _body(salon, start, f"race-guest-{n:02d}", email=f"race{n}@example.com")).status_code

    with ThreadPoolExecutor(max_workers=6) as pool:
        codes = list(pool.map(attempt, range(6)))
    assert codes.count(200) == 1, codes
    assert set(codes) == {200, 409}, codes
    assert len(_visits(db, salon["business"]["id"])) == 1


# BK-11: a retry returns the same visit, a changed body under the same key is refused

def test_retry_returns_the_same_visit_and_changed_body_is_refused(client, make_user, headers_for, db):
    salon = _salon(client, make_user, headers_for, "Retry")
    start, _day = _local_utc()
    body = _body(salon, start, "retry-key-0001")
    first = _ok(_book(client, salon, body), "first")
    second = _ok(_book(client, salon, body), "retry")
    assert second["id"] == first["id"]
    changed = _book(client, salon, dict(body, full_name="Someone Else"))
    assert changed.status_code == 409, changed.text
    later, _ = _local_utc(hour=13)
    moved = _book(client, salon, dict(body, start=later))
    assert moved.status_code == 409, moved.text
    with ThreadPoolExecutor(max_workers=4) as pool:
        replays = list(pool.map(lambda _: _book(client, salon, body), range(4)))
    assert {r.status_code for r in replays} <= {200, 409}
    assert {r.json()["id"] for r in replays if r.status_code == 200} <= {first["id"]}
    assert len(_visits(db, salon["business"]["id"])) == 1


def test_same_key_in_two_salons_is_two_independent_bookings(client, make_user, headers_for):
    one = _salon(client, make_user, headers_for, "Uno")
    two = _salon(client, make_user, headers_for, "Due")
    start, _day = _local_utc()
    a = _ok(_book(client, one, _body(one, start, "shared-key-01")), "one")
    b = _ok(_book(client, two, _body(two, start, "shared-key-01")), "two")
    assert a["id"] != b["id"]


# BK-12: payload caps and per-IP limits

def test_payload_caps(client, make_user, headers_for):
    salon = _salon(client, make_user, headers_for, "Caps")
    start, _day = _local_utc()
    cases = {
        "long name": _body(salon, start, "long-name-01", full_name="x" * 256),
        "long key": _body(salon, start, "k" * 65),
        "long phone": {**{k: v for k, v in _body(salon, start, "long-phone-1").items() if k != "email"}, "phone": "1" * 51},
        "chain too long": {**{k: v for k, v in _body(salon, start, "long-chain-1").items() if k != "service_id"},
                           "service_ids": [salon["service"]["id"]] * 21},
    }
    for label, body in cases.items():
        assert _book(client, salon, body).status_code == 422, label
    too_many = [salon["service"]["id"]] * 21
    response = client.get(f"/public/booking/{salon['slug']}/chain-slots", params={
        "service_ids": too_many, "date_from": _day.isoformat(),
    })
    assert response.status_code == 422, response.text


LIMITS = """
import json
from fastapi.testclient import TestClient
from app.main import app
c = TestClient(app)
body = {"service_id": 1, "staff_id": 1, "start": "2030-01-01T10:00:00Z", "full_name": "Probe",
        "email": "probe@example.com", "processing_consent": True, "client_request_id": "probe-key-1"}
card = [c.get("/public/booking/no-such-salon").status_code for _ in range(61)]
slots = [c.get("/public/booking/no-such-salon/slots", params={"service_id": 1, "date_from": "2030-01-01"}).status_code for _ in range(31)]
posts = [c.post("/public/booking/no-such-salon/bookings", json=body).status_code for _ in range(6)]
print(json.dumps({"card": card, "slots": slots, "posts": posts}))
"""


def test_public_booking_is_rate_limited_per_ip():
    # unknown slugs count too: guessing salons is exactly what the limit is for
    result = json.loads(_run(LIMITS))
    assert result["card"] == [404] * 60 + [429]
    assert result["slots"] == [404] * 30 + [429]
    assert result["posts"] == [404] * 5 + [429]
