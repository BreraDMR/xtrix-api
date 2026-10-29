"""Every committed cancellation cycle keeps its own event and notification."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app.api.core import booking_manage_access, booking_manage_policy, notification_events, waitlist_service
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.user import User
from app.db.models.visit import Visit
from app.db.models.visit_event import VisitEvent
from app.main import app
from tests import test_visitor_cancel_core as base

world = base.world


@pytest.fixture(params=['visitor', 'public'])
def cancellation(request, db, world, monkeypatch):
    monkeypatch.setattr(booking_manage_policy, "utcnow", lambda: world["clock"][0])
    if request.param == 'visitor':
        return f"/visitor/visits/{world['visit']}/cancel", {'X-Visitor-Token': world['raw']}, 204
    raw, _token = booking_manage_access.issue_token(db, db.get(Visit, world['visit']))
    return '/public/booking-management/cancel', {'X-Booking-Token': raw}, 200


def _reopen(http, db, world, headers_for):
    actor = headers_for(db.get(User, world['staff']))
    db.rollback()
    response = http.put(f"/visits/{world['visit']}", headers=actor, json={'status': 'planned'})
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'planned'


def test_staff_reopen_allows_new_cancel_but_same_state_repeat_is_noop(db, world, cancellation, headers_for, monkeypatch):
    matched = []
    monkeypatch.setattr(waitlist_service, 'release_slot_quietly', lambda *args: matched.append(True))
    url, headers, status = cancellation
    with TestClient(app, raise_server_exceptions=False) as http:
        assert http.post(url, headers=headers).status_code == status
        with Session(db.get_bind()) as writer:
            previous = writer.query(NotificationOutbox).filter_by(kind='booking_cancelled').one()
            previous.status = 'sent'
            writer.commit()
        _reopen(http, db, world, headers_for)
        second = http.post(url, headers=headers)
        assert second.status_code == status, second.text
        with Session(db.get_bind()) as observer:
            before = base.state(observer, world)
            events = observer.query(VisitEvent).filter_by(visit_id=world['visit']).all()
            messages = observer.query(NotificationOutbox).filter_by(kind='booking_cancelled').order_by(NotificationOutbox.id).all()
            assert observer.get(Visit, world['visit']).status == 'cancelled'
            assert len(events) == len(messages) == 2
            assert len({row.event_key for row in events}) == 2
            assert {row.event_key for row in events} == {row.event_key for row in messages}
            assert [row.status for row in messages] == ['sent', 'pending']
        assert http.post(url, headers=headers).status_code == status
        with Session(db.get_bind()) as observer:
            assert base.state(observer, world) == before
    assert matched == [True, True]


def test_second_cycle_deferred_commit_failure_rolls_back_event_message_and_status(db, world, cancellation, headers_for, monkeypatch):
    monkeypatch.setattr(waitlist_service, 'release_slot_quietly', lambda *args: None)
    url, headers, status = cancellation
    with TestClient(app, raise_server_exceptions=False) as http:
        assert http.post(url, headers=headers).status_code == status
        _reopen(http, db, world, headers_for)
        db.execute(text("""CREATE FUNCTION reject_second_cycle() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'synthetic second cycle commit failure'; RETURN NEW; END $$"""))
        db.execute(text('''CREATE CONSTRAINT TRIGGER reject_second_cycle AFTER INSERT ON visit_events
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION reject_second_cycle()'''))
        db.commit()
        before = base.state(db, world)
        db.rollback()
        errors = []
        def capture_error(context):
            errors.append(str(context.original_exception))
        event.listen(db.get_bind(), "handle_error", capture_error)
        try:
            response = http.post(url, headers=headers)
            assert response.status_code == 500, response.text
            assert any("synthetic second cycle commit failure" in error for error in errors)
            with Session(db.get_bind()) as observer:
                assert base.state(observer, world) == before
        finally:
            event.remove(db.get_bind(), "handle_error", capture_error)
            db.rollback()
            db.execute(text('DROP TRIGGER reject_second_cycle ON visit_events'))
            db.execute(text('DROP FUNCTION reject_second_cycle()'))
            db.commit()


def test_standalone_notification_without_operation_key_stays_idempotent(db, world):
    visit = db.get(Visit, world['visit'])
    business = db.get(Business, world['business'])
    person = db.get(Client, world['client'])
    notification_events.queue_booking_cancelled(db, business, visit, person)
    db.commit()
    row = db.query(NotificationOutbox).filter_by(kind='booking_cancelled').one()
    row.status = 'sent'
    db.commit()
    original_id = row.id
    notification_events.queue_booking_cancelled(db, business, visit, person)
    db.commit()
    rows = db.query(NotificationOutbox).filter_by(kind='booking_cancelled').all()
    assert len(rows) == 1 and rows[0].id == original_id and rows[0].status == 'sent'
