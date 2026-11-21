"""Staff create, move or cancel a visit in the CRM -> the client gets the same message a guest would.

Rows go into the outbox in the visit's own transaction. Past visits never produce a message,
saving the same thing twice doesn't produce a second one, and the salon can switch the
whole thing off (it is on by default because these messages are transactional).
"""
from datetime import timedelta

import pytest

from app.api.core import notification_events, notification_jobs, notification_transport
from app.api.core.clock import utcnow
from app.api.core.config import settings
from app.db.models.notification_outbox import NotificationOutbox
from tests.tenant_world import build_tenant


@pytest.fixture
def capture(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.invalid")
    monkeypatch.setattr(settings, "smtp_from", "notifications@example.com")
    monkeypatch.setattr(settings, "notification_transport", "capture")
    monkeypatch.setattr(notification_transport, "_transport", None)
    return notification_transport.get_notification_transport()


@pytest.fixture
def salon(client, make_user, headers_for, capture):
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070071")
    r = client.put("/businesses/me/notification-settings", json={"is_enabled": True}, headers=a["headers"])
    assert r.status_code == 200, r.text
    return a


def _ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def _create(client, salon, start):
    return _ok(client.post("/visits/", headers=salon["headers"], json={
        "client_id": salon["client"]["id"], "specialist_id": salon["owner"].id,
        "date_start": start.isoformat(), "date_end": (start + timedelta(hours=1)).isoformat()}))


def _move(client, salon, visit_id, start):
    return _ok(client.put(f"/visits/{visit_id}", headers=salon["headers"], json={
        "date_start": start.isoformat(), "date_end": (start + timedelta(hours=1)).isoformat()}))


def _kinds(db, visit_id):
    db.expire_all()
    rows = (db.query(NotificationOutbox)
            .filter(NotificationOutbox.visit_id == visit_id, NotificationOutbox.kind != notification_events.KIND_REMINDER)
            .order_by(NotificationOutbox.id).all())
    return [row.kind for row in rows]


def _future(days=5, hour=11):
    return (utcnow() + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)


def test_created_in_the_crm_queues_a_confirmation(client, db, salon):
    visit = _create(client, salon, _future())
    assert _kinds(db, visit["id"]) == [notification_events.KIND_CREATED]
    row = db.query(NotificationOutbox).filter(NotificationOutbox.visit_id == visit["id"],
                                              NotificationOutbox.kind == notification_events.KIND_CREATED).one()
    assert row.recipient == "alpha.client@example.com"
    assert row.payload["source"] == "crm"


def test_confirmation_is_rendered_and_delivered_by_the_job(client, db, salon, capture):
    capture.clear()
    _create(client, salon, _future())
    stats = notification_jobs.sync_and_deliver()["delivery"]
    assert stats["captured"] >= 1
    subjects = [m.subject for m in capture.messages()]
    assert "Запись создана" in subjects or "Booking received" in subjects


def test_past_visit_gets_nothing(client, db, salon):
    past = (utcnow() - timedelta(days=2)).replace(microsecond=0)
    visit = _create(client, salon, past)
    assert _kinds(db, visit["id"]) == []
    _ok(client.put(f"/visits/{visit['id']}", headers=salon["headers"], json={"status": "cancelled"}))
    assert _kinds(db, visit["id"]) == []


def test_move_queues_one_change_and_a_repeated_save_adds_nothing(client, db, salon):
    visit = _create(client, salon, _future())
    _move(client, salon, visit["id"], _future(days=6))
    _move(client, salon, visit["id"], _future(days=6))
    _ok(client.put(f"/visits/{visit['id']}", headers=salon["headers"], json={"note": "bring towel"}))
    assert _kinds(db, visit["id"]) == [notification_events.KIND_CREATED, notification_events.KIND_CHANGED]


def test_move_into_the_past_is_not_announced(client, db, salon):
    visit = _create(client, salon, _future())
    _move(client, salon, visit["id"], (utcnow() - timedelta(days=1)).replace(microsecond=0))
    assert _kinds(db, visit["id"]) == [notification_events.KIND_CREATED]


def test_cancel_queues_one_cancellation(client, db, salon):
    visit = _create(client, salon, _future())
    _ok(client.put(f"/visits/{visit['id']}", headers=salon["headers"], json={"status": "cancelled"}))
    _ok(client.put(f"/visits/{visit['id']}", headers=salon["headers"], json={"status": "cancelled"}))
    assert _kinds(db, visit["id"]) == [notification_events.KIND_CREATED, notification_events.KIND_CANCELLED]


def test_salon_switch_turns_crm_messages_off(client, db, salon):
    _ok(client.put("/businesses/me/notification-settings", json={"crm_visit_messages_enabled": False},
                   headers=salon["headers"]))
    visit = _create(client, salon, _future())
    _move(client, salon, visit["id"], _future(days=6))
    _ok(client.put(f"/visits/{visit['id']}", headers=salon["headers"], json={"status": "cancelled"}))
    assert _kinds(db, visit["id"]) == []


def test_notifications_off_means_no_crm_messages(client, db, salon):
    _ok(client.put("/businesses/me/notification-settings", json={"is_enabled": False}, headers=salon["headers"]))
    visit = _create(client, salon, _future())
    assert _kinds(db, visit["id"]) == []


def test_switch_turned_off_after_queueing_stops_delivery(client, db, salon, capture, monkeypatch):
    monkeypatch.setattr(settings, "notification_immediate_delivery", False)
    capture.clear()
    visit = _create(client, salon, _future())
    _ok(client.put("/businesses/me/notification-settings", json={"crm_visit_messages_enabled": False},
                   headers=salon["headers"]))
    notification_jobs.sync_and_deliver()
    db.expire_all()
    row = db.query(NotificationOutbox).filter(NotificationOutbox.visit_id == visit["id"],
                                              NotificationOutbox.kind == notification_events.KIND_CREATED).one()
    assert row.status == "cancelled"
    assert all(m.subject not in ("Запись создана", "Booking received") for m in capture.messages())
