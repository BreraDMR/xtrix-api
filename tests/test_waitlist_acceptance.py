"""WAIT-09 acceptance: an offer never holds a slot, never hangs forever, and racing
confirmations end with exactly one visit."""
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from app.api.core import waitlist_service
from app.db.models.business import Business
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.visit import Visit
from app.db.models.waitlist import WaitlistEntry, WaitlistOffer
from app.db.session import SessionLocal
from tests import test_waitlist as base
from tests.test_waitlist import _cancel, _headers, _join, _offer_rows, _offer_token, _visit

salon = base.salon


def _live_visits(db):
    db.expire_all()
    return db.query(Visit).filter(Visit.status != "cancelled", Visit.deleted_at.is_(None)).all()


def _offer_for(db, business_id, email):
    db.expire_all()
    return (
        db.query(WaitlistOffer)
        .join(WaitlistEntry, WaitlistEntry.id == WaitlistOffer.entry_id)
        .filter(WaitlistOffer.business_id == business_id, WaitlistEntry.email == email)
        .order_by(WaitlistOffer.id.desc())
        .first()
    )


def _mail_status(db, offer_id):
    db.expire_all()
    row = db.query(NotificationOutbox).filter(
        NotificationOutbox.event_key == f"waitlist_offer:{offer_id}"
    ).one()
    return row.status


def _release(db, salon, start):
    return waitlist_service.release_slot(
        db, db.get(Business, salon["business"]["id"]), salon["service"]["id"], salon["owner"].id, start
    )


def test_pending_offer_does_not_hold_the_slot(client, db, salon):
    assert _join(client, salon, email="nohold@example.com").status_code == 202
    visit = _visit(client, salon)
    assert _cancel(client, salon, visit["id"]).status_code == 200
    token = _offer_token(db, salon["business"]["id"], "nohold@example.com")

    # the salon books the same time by hand while the offer is still pending
    taken = _visit(client, salon)
    accepted = client.post("/public/waitlist/offer/accept", headers=_headers(token))
    assert accepted.status_code == 409, accepted.text

    offer = _offer_for(db, salon["business"]["id"], "nohold@example.com")
    assert offer.status == "conflict" and offer.visit_id is None
    assert db.get(WaitlistEntry, offer.entry_id).status == "active"
    assert [row.id for row in _live_visits(db)] == [taken["id"]]


def test_expired_offer_lets_the_entry_get_the_next_slot(client, db, salon):
    assert _join(client, salon, email="again@example.com", time_preference="any").status_code == 202
    visit = _visit(client, salon)
    assert _cancel(client, salon, visit["id"]).status_code == 200
    first = _offer_for(db, salon["business"]["id"], "again@example.com")
    assert first.status == "pending"

    # nothing else is offered to the same person while one offer is pending
    later = salon["start"] + timedelta(hours=2)
    assert _release(db, salon, later) == []

    first.expires_at = first.created_at - timedelta(seconds=1)
    db.commit()
    offers = _release(db, salon, later)
    assert len(offers) == 1 and offers[0].start == later
    db.expire_all()
    assert db.get(WaitlistOffer, first.id).status == "expired"
    assert _mail_status(db, first.id) == "cancelled"
    assert db.get(WaitlistEntry, first.entry_id).status == "active"


def test_one_free_slot_is_offered_to_at_most_three_people(client, db, salon):
    for n in range(4):
        assert _join(client, salon, email=f"cap{n}@example.com", name=f"Cap {n}").status_code == 202
    visit = _visit(client, salon)
    assert _cancel(client, salon, visit["id"]).status_code == 200
    offers = _offer_rows(db, salon["business"]["id"])
    assert len(offers) == 3
    emails = {db.get(WaitlistEntry, offer.entry_id).email for offer in offers}
    assert emails == {"cap0@example.com", "cap1@example.com", "cap2@example.com"}
    assert db.query(NotificationOutbox).filter(NotificationOutbox.kind == "waitlist_offer").count() == 3


def test_three_racing_confirmations_book_once_and_close_the_rest(client, db, salon):
    emails = [f"race{n}@example.com" for n in range(3)]
    for email in emails:
        assert _join(client, salon, email=email, name=email.split("@")[0]).status_code == 202
    visit = _visit(client, salon)
    assert _cancel(client, salon, visit["id"]).status_code == 200
    tokens = [_offer_token(db, salon["business"]["id"], email) for email in emails]
    barrier = threading.Barrier(3)

    def confirm(raw_token):
        session = SessionLocal()
        try:
            barrier.wait(timeout=10)
            try:
                return ("booked", waitlist_service.accept_offer(session, raw_token, "en").id)
            except waitlist_service.OfferConflict:
                return ("conflict", None)
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(confirm, tokens))

    booked = [visit_id for status, visit_id in results if status == "booked"]
    assert len(booked) == 1, results
    assert [row.id for row in _live_visits(db)] == booked
    offers = _offer_rows(db, salon["business"]["id"])
    assert sorted(offer.status for offer in offers) == ["accepted", "conflict", "conflict"]
    for offer in offers:
        if offer.status == "conflict":
            # losers stay in the queue and their unsent mail is dropped
            assert db.get(WaitlistEntry, offer.entry_id).status == "active"
            assert _mail_status(db, offer.id) in ("cancelled", "sent", "captured")


def test_confirm_and_leave_at_once_never_book_a_cancelled_entry(client, db, salon):
    for round_no in range(3):
        email = f"leave{round_no}@example.com"
        assert _join(client, salon, email=email).status_code == 202
        visit = _visit(client, salon)
        assert _cancel(client, salon, visit["id"]).status_code == 200
        token = _offer_token(db, salon["business"]["id"], email)
        barrier = threading.Barrier(2)

        def accept():
            session = SessionLocal()
            try:
                barrier.wait(timeout=10)
                try:
                    return waitlist_service.accept_offer(session, token, "en").id
                except (waitlist_service.EntryClosed, waitlist_service.OfferUnavailable):
                    return None
            finally:
                session.close()

        def leave():
            session = SessionLocal()
            try:
                barrier.wait(timeout=10)
                try:
                    return waitlist_service.leave_waitlist(session, token).status
                except waitlist_service.EntryClosed:
                    return "closed"
            finally:
                session.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            booked_future = pool.submit(accept)
            left_future = pool.submit(leave)
            booked, left = booked_future.result(), left_future.result()

        offer = _offer_for(db, salon["business"]["id"], email)
        entry = db.get(WaitlistEntry, offer.entry_id)
        if booked:
            assert left == "closed" and entry.status == "fulfilled" and offer.status == "accepted"
            # free the time again for the next round
            assert _cancel(client, salon, booked).status_code == 200
        else:
            assert left == "cancelled" and entry.status == "cancelled" and offer.status == "cancelled"
        assert len(_live_visits(db)) == 0


def test_slot_freed_again_reaches_people_who_lost_the_race(client, db, salon):
    assert _join(client, salon, email="winner@example.com", name="Winner").status_code == 202
    assert _join(client, salon, email="loser@example.com", name="Loser").status_code == 202
    visit = _visit(client, salon)
    assert _cancel(client, salon, visit["id"]).status_code == 200
    winner = _offer_token(db, salon["business"]["id"], "winner@example.com")
    booked = client.post("/public/waitlist/offer/accept", headers=_headers(winner))
    assert booked.status_code == 200, booked.text
    assert _offer_for(db, salon["business"]["id"], "loser@example.com").status == "conflict"

    # the winner cancels the booked visit: the time is free again for the one still waiting
    assert _cancel(client, salon, booked.json()["id"]).status_code == 200
    again = _offer_for(db, salon["business"]["id"], "loser@example.com")
    assert again.status == "pending", again.status
    assert again.start == salon["start"]
