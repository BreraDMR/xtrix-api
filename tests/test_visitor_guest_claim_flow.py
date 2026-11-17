"""CPA-16 end to end: a real guest booking, later attached to a visitor account.

The guest books through the public form with no account at all. Afterwards a
logged-in visitor posts the manage token from the booking answer. The same
Visit and Client rows must show up in the cabinet, nothing gets copied, and
the session can't be pointed at someone else's ids.
"""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.api.core import visitor_sessions
from app.api.core.clock import utcnow
from app.db.models.client import Client
from app.db.models.visit import Visit
from app.db.models.visitor_access import VisitorClaim, VisitorGrant
from app.db.models.visitor_account import VisitorAccount

ZONE = ZoneInfo("Europe/Prague")
GUEST_EMAIL = "guest.claim@example.com"


def _ok(response, what):
    assert response.status_code in (200, 201), f"{what}: {response.status_code} {response.text}"
    return response.json()


@pytest.fixture
def salon(client, make_user, headers_for):
    owner = make_user("business_owner", full_name="Claim Owner", email="claim.owner@example.com")
    headers = headers_for(owner)
    business = _ok(client.post("/businesses/", headers=headers, json={
        "name": "Claim Salon", "business_type": "salon", "timezone": "Europe/Prague"}), "business")
    public = _ok(client.put("/businesses/me/public-booking", headers=headers,
                            json={"is_enabled": True}), "public settings")
    service = _ok(client.post("/services/", headers=headers, json={
        "name": "Claim Cut", "price": 500, "duration_minutes": 30, "specialist_id": owner.id}), "service")
    day = (utcnow() + timedelta(days=5)).astimezone(ZONE).date()
    _ok(client.post("/schedules/work", headers=headers, json={
        "weekday": day.weekday(), "start_time": "09:00", "end_time": "17:00",
        "specialist_id": owner.id}), "schedule")
    start = datetime.combine(day, time(10, 0), tzinfo=ZONE).astimezone(timezone.utc).replace(tzinfo=None)
    return {"owner": owner, "headers": headers, "business": business,
            "slug": public["slug"], "service": service, "start": start}


def _book(client, salon, hour_shift, request_id, headers=None):
    start = salon["start"] + timedelta(hours=hour_shift)
    return _ok(client.post(f"/public/booking/{salon['slug']}/bookings", headers=headers or {}, json={
        "service_id": salon["service"]["id"], "staff_id": salon["owner"].id,
        "start": start.isoformat() + "Z", "full_name": "Guest Claim", "email": GUEST_EMAIL,
        "processing_consent": True, "client_request_id": request_id}), "guest booking")


def _visitor(db, email):
    account = VisitorAccount(email_canonical=email, email_verified_at=utcnow())
    db.add(account)
    db.flush()
    raw = visitor_sessions.create_session(db, account)[1]
    account_id = account.id
    db.commit()
    return account_id, {"X-Visitor-Token": raw}


def _visit_row(db, visit_id):
    db.expire_all()
    return dict(db.execute(select(Visit.__table__).where(Visit.id == visit_id)).mappings().one())


def _cabinet_ids(client, headers, **params):
    response = client.get("/visitor/visits", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return [row["id"] for row in response.json()["entries"]]


def test_guest_books_without_account_then_claim_attaches_same_rows(client, db, salon):
    me, mine = _visitor(db, GUEST_EMAIL)
    # A visitor token sent along with a guest booking is just ignored: booking
    # stays anonymous and doesn't silently attach anything.
    first = _book(client, salon, 0, "claim-flow-1", headers=mine)
    visit_id = first["id"]
    assert db.query(VisitorGrant).count() == 0
    assert _cabinet_ids(client, mine) == []
    visits, clients = db.query(Visit).count(), db.query(Client).count()
    before = _visit_row(db, visit_id)

    claim = client.post("/visitor/claims/manage", headers=mine, json={"token": first["manage_token"]})
    assert claim.status_code == 204, claim.text
    again = client.post("/visitor/claims/manage", headers=mine, json={"token": first["manage_token"]})
    assert again.status_code == 204, again.text

    assert db.query(Visit).count() == visits and db.query(Client).count() == clients
    assert _visit_row(db, visit_id) == before
    grant, = db.query(VisitorGrant).all()
    assert (grant.account_id, grant.scope, grant.visit_id) == (me, "visit", visit_id)
    assert (grant.business_id, grant.client_id) == (before["business_id"], before["client_id"])
    assert db.query(VisitorClaim).count() == 1
    assert _cabinet_ids(client, mine) == [visit_id]
    detail = client.get(f"/visitor/visits/{visit_id}", headers=mine)
    assert detail.status_code == 200, detail.text
    assert detail.json()["id"] == visit_id

    # The old guest link keeps working after the claim.
    guest = client.get("/public/booking-management", headers={"X-Booking-Token": first["manage_token"]})
    assert guest.status_code == 200, guest.text


def test_second_guest_booking_reuses_client_and_needs_its_own_claim(client, db, salon):
    _me, mine = _visitor(db, GUEST_EMAIL)
    first = _book(client, salon, 0, "claim-flow-a")
    clients = db.query(Client).count()
    second = _book(client, salon, 2, "claim-flow-b")
    assert db.query(Client).count() == clients
    first_id = first["id"]
    second_id = second["id"]

    assert client.post("/visitor/claims/manage", headers=mine,
                       json={"token": first["manage_token"]}).status_code == 204
    # One visit's link opens only that visit, not the whole client card.
    assert _cabinet_ids(client, mine) == [first_id]
    assert client.get(f"/visitor/visits/{second_id}", headers=mine).status_code == 404

    assert client.post("/visitor/claims/manage", headers=mine,
                       json={"token": second["manage_token"]}).status_code == 204
    assert sorted(_cabinet_ids(client, mine)) == sorted([first_id, second_id])
    assert db.query(Client).count() == clients
    assert db.query(VisitorGrant).count() == 2


def test_session_cannot_reach_or_reassign_foreign_ids(client, db, salon):
    _me, mine = _visitor(db, GUEST_EMAIL)
    _other, theirs = _visitor(db, "someone.else@example.com")
    own = _book(client, salon, 0, "claim-flow-own")
    foreign = _book(client, salon, 3, "claim-flow-foreign")
    own_id = own["id"]
    foreign_id = foreign["id"]
    assert client.post("/visitor/claims/manage", headers=theirs,
                       json={"token": foreign["manage_token"]}).status_code == 204
    foreign_row = _visit_row(db, foreign_id)

    # Ids in the body are refused outright, they never pick the target.
    spoofs = {"business_id": foreign_row["business_id"], "client_id": foreign_row["client_id"],
              "visit_id": foreign_id, "account_id": _other}
    for extra, value in spoofs.items():
        response = client.post("/visitor/claims/manage", headers=mine,
                               json={"token": own["manage_token"], extra: value})
        assert response.status_code == 422, response.text
    assert client.post("/visitor/claims/manage", headers=mine,
                       json={"token": own["manage_token"]}).status_code == 204
    # Someone else's visit stays invisible and the spent proof can't move it over.
    assert client.get(f"/visitor/visits/{foreign_id}", headers=mine).status_code == 404
    assert client.post("/visitor/claims/manage", headers=mine,
                       json={"token": foreign["manage_token"]}).status_code == 409
    assert _cabinet_ids(client, mine) == [own_id]
    assert _cabinet_ids(client, theirs) == [foreign_id]
    assert _cabinet_ids(client, mine, business_id=salon["business"]["id"] + 999) == []
    assert _visit_row(db, foreign_id) == foreign_row
