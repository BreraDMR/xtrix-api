"""Waitlist: dedupe, matching, time-limited offers and atomic acceptance."""
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta, timezone
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import pytest

from app.api.core import waitlist_service
from app.api.core.clock import utcnow
from app.api.core.config import settings
from app.api.core.notification_templates import render_outbox_notification
from app.db.models.business import Business
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.visit import Visit
from app.db.models.waitlist import WaitlistEntry, WaitlistOffer
from app.db.session import SessionLocal

ZONE = ZoneInfo("Europe/Prague")


def _ok(response, what):
    assert response.status_code in (200, 201, 202), f"{what}: {response.status_code} {response.text}"
    return response.json()


@pytest.fixture
def salon(client, db, make_user, headers_for, monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.test.invalid")
    monkeypatch.setattr(settings, "smtp_from", "waitlist@test.invalid")
    owner = make_user("business_owner", full_name="Wait Owner", email="wait.owner@example.com")
    headers = headers_for(owner)
    business = _ok(
        client.post(
            "/businesses/",
            json={"name": "Wait Salon", "business_type": "salon", "timezone": "Europe/Prague"},
            headers=headers,
        ),
        "business",
    )
    public = _ok(
        client.put(
            "/businesses/me/public-booking", json={"is_enabled": True}, headers=headers
        ),
        "public settings",
    )
    service = _ok(
        client.post(
            "/services/",
            json={
                "name": "Wait Haircut",
                "price": 40,
                "duration_minutes": 60,
                "specialist_id": owner.id,
            },
            headers=headers,
        ),
        "service",
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
    notifications = _ok(
        client.put(
            "/businesses/me/notification-settings",
            json={"is_enabled": True},
            headers=headers,
        ),
        "notification settings",
    )
    assert notifications["is_enabled"] is True
    start_local = datetime.combine(local_day, time(10, 0), tzinfo=ZONE)
    start = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    guest = _ok(
        client.post(
            "/clients/",
            json={"full_name": "Booked Guest", "contact_email": "booked.guest@example.com"},
            headers=headers,
        ),
        "client",
    )
    return {
        "owner": owner,
        "headers": headers,
        "business": business,
        "slug": public["slug"],
        "service": service,
        "local_day": local_day,
        "start": start,
        "client": guest,
        "notifications": notifications,
    }


def _join(client, salon, email="wait.guest@example.com", name="Wait Guest", **overrides):
    payload = {
        "service_id": salon["service"]["id"],
        "staff_id": salon["owner"].id,
        "preferred_date_from": salon["local_day"].isoformat(),
        "preferred_date_to": salon["local_day"].isoformat(),
        "time_preference": "morning",
        "full_name": name,
        "email": email,
        "processing_consent": True,
    }
    payload.update(overrides)
    return client.post(f"/public/waitlist/{salon['slug']}/join", json=payload)


def _visit(client, salon, start=None):
    start = start or salon["start"]
    return _ok(
        client.post(
            "/visits/",
            json={
                "client_id": salon["client"]["id"],
                "specialist_id": salon["owner"].id,
                "service_id": salon["service"]["id"],
                "date_start": start.isoformat(),
                "date_end": (start + timedelta(hours=1)).isoformat(),
                "price": 40,
                "payment_method": "cash",
            },
            headers=salon["headers"],
        ),
        "visit",
    )


def _cancel(client, salon, visit_id):
    return client.put(
        f"/visits/{visit_id}", json={"status": "cancelled"}, headers=salon["headers"]
    )


def _offer_rows(db, business_id):
    db.expire_all()
    return (
        db.query(WaitlistOffer)
        .filter(WaitlistOffer.business_id == business_id)
        .order_by(WaitlistOffer.id)
        .all()
    )


def _offer_token(db, business_id, recipient):
    db.expire_all()
    row = (
        db.query(NotificationOutbox)
        .filter(
            NotificationOutbox.business_id == business_id,
            NotificationOutbox.kind == "waitlist_offer",
            NotificationOutbox.recipient == recipient,
        )
        .order_by(NotificationOutbox.id.desc())
        .first()
    )
    assert row is not None, f"no waitlist offer mail for {recipient}"
    message = render_outbox_notification(db, row)
    match = re.search(r"https?://\S+/waitlist/offer#token=\S+", message.text)
    assert match, message.text
    token = parse_qs(urlsplit(match.group(0)).fragment)["token"][0]
    assert token in message.html
    return token


def _headers(token):
    return {"X-Waitlist-Token": token}


def test_join_repeat_and_leave(client, db, make_user, headers_for, salon):
    first = _join(client, salon, email="repeat.guest@example.com")
    assert first.status_code == 202, first.text
    body = first.json()
    assert body["created"] is True
    assert body["manage_token"]
    assert "position" not in body and "total" not in body

    second = _join(client, salon, email="repeat.guest@example.com")
    assert second.status_code == 202, second.text
    assert second.json()["created"] is False
    assert second.json()["manage_token"] is None
    assert db.query(WaitlistEntry).filter(WaitlistEntry.status == "active").count() == 1

    other = _join(client, salon, email="other.guest@example.com", name="Other Guest")
    assert other.status_code == 202 and other.json()["created"] is True
    assert db.query(WaitlistEntry).filter(WaitlistEntry.status == "active").count() == 2

    left = client.post("/public/waitlist/leave", headers=_headers(body["manage_token"]))
    assert left.status_code == 200, left.text
    assert db.query(WaitlistEntry).filter(WaitlistEntry.status == "active").count() == 1

    again = _join(client, salon, email="repeat.guest@example.com")
    assert again.status_code == 202
    assert again.json()["created"] is True
    assert again.json()["manage_token"]

    unknown = client.post("/public/waitlist/leave", headers=_headers("nonsense"))
    assert unknown.status_code == 404


def test_matching_respects_service_date_and_time_of_day(client, db, make_user, headers_for, salon):
    morning = _join(client, salon, email="morning.guest@example.com")
    assert morning.status_code == 202
    afternoon = _join(
        client, salon, email="afternoon.guest@example.com", time_preference="afternoon"
    )
    assert afternoon.status_code == 202
    later_day = (salon["local_day"] + timedelta(days=1)).isoformat()
    later = _join(
        client,
        salon,
        email="later.guest@example.com",
        preferred_date_from=later_day,
        preferred_date_to=later_day,
    )
    assert later.status_code == 202

    visit = _visit(client, salon)
    assert _cancel(client, salon, visit["id"]).status_code == 200

    offers = _offer_rows(db, salon["business"]["id"])
    assert len(offers) == 1, [offer.status for offer in offers]
    entry = db.get(WaitlistEntry, offers[0].entry_id)
    assert entry.email == "morning.guest@example.com"
    assert offers[0].start == salon["start"]
    assert offers[0].status == "pending"

    mail = db.query(NotificationOutbox).filter(
        NotificationOutbox.kind == "waitlist_offer"
    ).all()
    assert len(mail) == 1

    # saving or releasing the same time again must not send another offer
    release = waitlist_service.release_slot(
        db,
        db.get(Business, salon["business"]["id"]),
        salon["service"]["id"],
        salon["owner"].id,
        salon["start"],
    )
    assert release == []
    assert len(_offer_rows(db, salon["business"]["id"])) == 1
    assert db.query(NotificationOutbox).filter(
        NotificationOutbox.kind == "waitlist_offer"
    ).count() == 1


def test_accept_creates_one_visit_and_is_replayable(client, db, make_user, headers_for, salon):
    joined = _join(client, salon, email="accept.guest@example.com")
    assert joined.status_code == 202
    visit = _visit(client, salon)
    assert _cancel(client, salon, visit["id"]).status_code == 200

    token = _offer_token(db, salon["business"]["id"], "accept.guest@example.com")
    summary = client.get("/public/waitlist/offer", headers=_headers(token))
    assert summary.status_code == 200, summary.text
    data = summary.json()
    assert data["status"] == "pending"
    assert data["service_name"] == "Wait Haircut"
    assert data["staff_name"] == salon["owner"].full_name

    accepted = client.post("/public/waitlist/offer/accept", headers=_headers(token))
    assert accepted.status_code == 200, accepted.text
    booking = accepted.json()
    assert booking["status"] == "planned"
    assert booking["manage_token"]

    offer = _offer_rows(db, salon["business"]["id"])[0]
    entry = db.get(WaitlistEntry, offer.entry_id)
    assert offer.status == "accepted" and offer.visit_id == booking["id"]
    assert entry.status == "fulfilled"
    assert db.query(Visit).filter(Visit.id == booking["id"]).count() == 1

    replay = client.post("/public/waitlist/offer/accept", headers=_headers(token))
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == booking["id"]
    assert db.query(Visit).filter(Visit.status != "cancelled").count() == 1

    later = client.get("/public/waitlist/offer", headers=_headers(token))
    assert later.status_code == 200 and later.json()["status"] == "accepted"

    leave = client.post("/public/waitlist/leave", headers=_headers(token))
    assert leave.status_code == 409


def test_offer_is_not_accepted_after_public_booking_is_switched_off(client, db, make_user, headers_for, salon):
    assert _join(client, salon, email="closed.guest@example.com").status_code == 202
    visit = _visit(client, salon)
    assert _cancel(client, salon, visit["id"]).status_code == 200
    token = _offer_token(db, salon["business"]["id"], "closed.guest@example.com")
    off = client.put("/businesses/me/public-booking", json={"is_enabled": False}, headers=salon["headers"])
    assert off.status_code == 200, off.text

    accepted = client.post("/public/waitlist/offer/accept", headers=_headers(token))
    assert accepted.status_code == 404, accepted.text
    assert db.query(Visit).filter(Visit.status != "cancelled").count() == 0
    offer = _offer_rows(db, salon["business"]["id"])[0]
    assert offer.status == "pending" and offer.visit_id is None


def test_two_offers_for_one_slot_accept_exactly_once(client, db, make_user, headers_for, salon):
    first = _join(client, salon, email="race.one@example.com", name="Race One")
    second = _join(client, salon, email="race.two@example.com", name="Race Two")
    assert first.status_code == 202 and second.status_code == 202
    visit = _visit(client, salon)
    assert _cancel(client, salon, visit["id"]).status_code == 200

    offers = _offer_rows(db, salon["business"]["id"])
    assert len(offers) == 2
    token_one = _offer_token(db, salon["business"]["id"], "race.one@example.com")
    token_two = _offer_token(db, salon["business"]["id"], "race.two@example.com")
    barrier = threading.Barrier(2)

    def confirm(raw_token):
        session = SessionLocal()
        try:
            barrier.wait(timeout=10)
            try:
                visit = waitlist_service.accept_offer(session, raw_token, "en")
                return ("booked", visit.id)
            except waitlist_service.OfferConflict:
                return ("conflict", None)
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(confirm, (token_one, token_two)))

    booked = [visit_id for status, visit_id in results if status == "booked"]
    conflicts = [status for status, _ in results if status == "conflict"]
    assert len(booked) == 1, results
    assert len(conflicts) == 1, results
    states = {offer.status for offer in _offer_rows(db, salon["business"]["id"])}
    assert states == {"accepted", "conflict"}
    assert db.query(Visit).filter(Visit.status != "cancelled").count() == 1


def test_same_offer_confirmed_twice_yields_one_booking(client, db, make_user, headers_for, salon, monkeypatch):
    joined = _join(client, salon, email="double.guest@example.com")
    assert joined.status_code == 202
    visit = _visit(client, salon)
    assert _cancel(client, salon, visit["id"]).status_code == 200
    token = _offer_token(db, salon["business"]["id"], "double.guest@example.com")

    barrier = threading.Barrier(2)

    def confirm():
        session = SessionLocal()
        try:
            barrier.wait(timeout=10)
            return waitlist_service.accept_offer(session, token, "en").id
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _x: confirm(), range(2)))
    assert ids[0] == ids[1]
    assert db.query(Visit).filter(Visit.status != "cancelled").count() == 1


def test_expired_offer_is_not_accepted(client, db, make_user, headers_for, salon, monkeypatch):
    _join(client, salon, email="expired.guest@example.com")
    visit = _visit(client, salon)
    assert _cancel(client, salon, visit["id"]).status_code == 200
    token = _offer_token(db, salon["business"]["id"], "expired.guest@example.com")
    offer = _offer_rows(db, salon["business"]["id"])[0]
    monkeypatch.setattr(
        waitlist_service, "utcnow", lambda: offer.expires_at + timedelta(seconds=1)
    )

    response = client.post("/public/waitlist/offer/accept", headers=_headers(token))
    assert response.status_code == 409, response.text
    db.expire_all()
    assert db.get(WaitlistOffer, offer.id).status == "expired"
    entry = db.get(WaitlistEntry, offer.entry_id)
    assert entry.status == "active"


def test_offer_can_be_declined_without_leaving(client, db, make_user, headers_for, salon):
    _join(client, salon, email="decline.guest@example.com")
    visit = _visit(client, salon)
    assert _cancel(client, salon, visit["id"]).status_code == 200
    token = _offer_token(db, salon["business"]["id"], "decline.guest@example.com")

    declined = client.post("/public/waitlist/offer/decline", headers=_headers(token))
    assert declined.status_code == 200, declined.text
    offer = _offer_rows(db, salon["business"]["id"])[0]
    assert offer.status == "declined"
    assert db.get(WaitlistEntry, offer.entry_id).status == "active"

    again = client.post("/public/waitlist/offer/decline", headers=_headers(token))
    assert again.status_code == 200
    accepted = client.post("/public/waitlist/offer/accept", headers=_headers(token))
    assert accepted.status_code == 409


def test_no_offer_when_notifications_are_off(client, db, make_user, headers_for, salon):
    _join(client, salon, email="quiet.guest@example.com")
    client.put(
        "/businesses/me/notification-settings",
        json={"is_enabled": False},
        headers=salon["headers"],
    )
    visit = _visit(client, salon)
    assert _cancel(client, salon, visit["id"]).status_code == 200
    assert _offer_rows(db, salon["business"]["id"]) == []


def test_salon_list_is_scoped_and_exposes_offer_state(client, db, make_user, headers_for, salon):
    _join(client, salon, email="list.guest@example.com", name="List Guest")
    visit = _visit(client, salon)
    assert _cancel(client, salon, visit["id"]).status_code == 200

    listed = _ok(
        client.get("/waitlist?status=active", headers=salon["headers"]), "waitlist list"
    )
    assert listed["total"] == 1
    item = listed["items"][0]
    assert item["email"] == "list.guest@example.com"
    assert item["service_name"] == "Wait Haircut"
    assert item["latest_offer"]["status"] == "pending"

    outsider = make_user("business_owner", full_name="Outsider", email="outsider@example.com")
    _ok(
        client.post(
            "/businesses/",
            json={"name": "Other Salon", "business_type": "salon"},
            headers=headers_for(outsider),
        ),
        "other business",
    )
    hidden = _ok(
        client.get("/waitlist", headers=headers_for(outsider)), "other list"
    )
    assert hidden["total"] == 0

    closed = client.post(f"/waitlist/{item['id']}/cancel", headers=salon["headers"])
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "cancelled"
    assert _offer_rows(db, salon["business"]["id"])[0].status == "cancelled"


def test_join_validates_consent_range_and_staff(client, db, make_user, headers_for, salon):
    no_consent = _join(client, salon, processing_consent=False)
    assert no_consent.status_code == 422
    bad_range = _join(
        client,
        salon,
        preferred_date_from=salon["local_day"].isoformat(),
        preferred_date_to=(salon["local_day"] - timedelta(days=1)).isoformat(),
    )
    assert bad_range.status_code == 422
    long_range = _join(
        client,
        salon,
        preferred_date_from=salon["local_day"].isoformat(),
        preferred_date_to=(salon["local_day"] + timedelta(days=40)).isoformat(),
    )
    assert long_range.status_code == 422
    stranger = _join(client, salon, staff_id=999999)
    assert stranger.status_code == 404
    missing_service = _join(client, salon, service_id=999999)
    assert missing_service.status_code == 404
    bad_time = _join(client, salon, time_preference="midnight")
    assert bad_time.status_code == 422
