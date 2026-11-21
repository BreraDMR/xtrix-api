"""MSG-12 acceptance: booking notifications, end to end through the capture transport.

A guest books, moves and cancels over the public API; the real job (`sync_and_deliver`) renders
and "sends" every message through capture, so nothing ever leaves the machine. One test per
"done" line of MSG-01..11, so a failure points straight at the card it breaks.
"""
import json
import logging
import smtplib
import threading
import time as clock
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

import pytest
import requests

from app.api.core import notification_events, notification_jobs, notification_scheduler
from app.api.core import notification_transport, notification_worker
from app.api.core.clock import utcnow
from app.api.core.config import settings
from app.api.core.notification_outbox import enqueue_notification
from app.api.core.notification_transport import DeliveryResult, OutboundMessage
from app.db.models.booking_manage_token import BookingManageToken
from app.db.models.business import Business
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.user import User
from app.db.models.visit import Visit
from app.db.session import SessionLocal
from tests.test_public_booking_acceptance import _body, _book, _local_utc, _ok, _salon

SECRET = "smtp-password-must-never-leak"


@pytest.fixture
def capture(monkeypatch):
    """SMTP looks configured, but every message stays in memory."""
    monkeypatch.setattr(settings, "smtp_host", "smtp.test.invalid")
    monkeypatch.setattr(settings, "smtp_from", "salon@test.invalid")
    monkeypatch.setattr(settings, "smtp_password", SECRET)
    monkeypatch.setattr(settings, "notification_transport", "capture")
    # these cards are about the job; the try-right-away path has its own tests
    monkeypatch.setattr(settings, "notification_immediate_delivery", False)
    monkeypatch.setattr(notification_transport, "_transport", None)
    transport = notification_transport.get_notification_transport()
    assert transport.name == "capture"
    return transport


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a network call was made")

    monkeypatch.setattr(smtplib, "SMTP", refuse)
    monkeypatch.setattr(requests, "post", refuse)


def _enable(client, salon, **extra):
    return _ok(client.put("/businesses/me/notification-settings",
                          json={"is_enabled": True, **extra}, headers=salon["headers"]), "enable")


def _rows(db, visit_id, kind=None):
    db.expire_all()
    query = db.query(NotificationOutbox).filter(NotificationOutbox.visit_id == visit_id)
    if kind:
        query = query.filter(NotificationOutbox.kind == kind)
    return query.order_by(NotificationOutbox.id).all()


def _deliver(now=None):
    """The real job when `now` is not given; otherwise the worker alone at that moment."""
    if now is None:
        return notification_jobs.sync_and_deliver()["delivery"]
    db = SessionLocal()
    try:
        from app.api.core import notification_templates
        return notification_worker.run_once(
            db, lambda row: notification_templates.send_outbox_message(db, row), now=now)
    finally:
        db.close()


def _token_from(message):
    raw = message.text.split("/manage#token=", 1)[1].split()[0]
    return unquote(raw)


def _booked_visit(client, db, salon, *, language="en", name="Guest Example", key="msg-key-0001", days=5):
    start, _day = _local_utc(days_ahead=days)
    response = client.post(f"/public/booking/{salon['slug']}/bookings",
                           json=_body(salon, start, key, full_name=name),
                           headers={"Accept-Language": language})
    booked = _ok(response, "booking")
    db.expire_all()
    visit = db.query(Visit).filter(Visit.business_id == salon["business"]["id"],
                                   Visit.deleted_at.is_(None)).order_by(Visit.id.desc()).first()
    return booked, visit


# MSG-01: capture never touches the network, and no provider never looks like success

def test_capture_keeps_messages_in_memory_and_is_not_a_delivery(capture, no_network):
    result = capture.send(OutboundMessage(channel="email", recipient="guest@example.com",
                                          subject="Hi", text="Body"))
    assert (result.status, result.delivered) == ("captured", False)
    assert [m.recipient for m in capture.messages()] == ["guest@example.com"]


def test_live_default_without_provider_reports_skipped_not_sent(monkeypatch, no_network):
    monkeypatch.setattr(settings, "notification_transport", "")
    monkeypatch.setattr(settings, "smtp_host", "")
    monkeypatch.setattr(notification_transport, "_transport", None)
    transport = notification_transport.get_notification_transport()
    assert transport.name == "live"
    result = transport.send(OutboundMessage(channel="email", recipient="guest@example.com",
                                            subject="Hi", text="Body"))
    assert (result.status, result.error, result.delivered) == ("skipped", "smtp_not_configured", False)


# MSG-07: off by default, no secrets out, a provider that cannot deliver is not "ready"

def test_settings_are_off_by_default_and_never_show_provider_secrets(client, make_user, headers_for, capture):
    salon = _salon(client, make_user, headers_for, "Quiet")
    response = client.get("/businesses/me/notification-settings", headers=salon["headers"])
    current = _ok(response, "settings")
    assert current["is_enabled"] is False
    assert current["provider_ready"] is True
    assert SECRET not in response.text and "smtp.test.invalid" not in response.text
    assert set(current) == {"channel", "reminder_time", "is_enabled", "provider_ready", "available_channels",
                            "birthday_greetings_enabled", "visit_followups_enabled", "crm_visit_messages_enabled"}


def test_switch_cannot_be_turned_on_without_a_provider(client, make_user, headers_for, monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "")
    salon = _salon(client, make_user, headers_for, "Unwired")
    current = _ok(client.get("/businesses/me/notification-settings", headers=salon["headers"]), "settings")
    assert (current["provider_ready"], current["available_channels"]) == (False, [])
    response = client.put("/businesses/me/notification-settings", json={"is_enabled": True}, headers=salon["headers"])
    assert response.status_code == 409
    response = client.put("/businesses/me/notification-settings", json={"channel": "sms"}, headers=salon["headers"])
    assert response.status_code == 409


def test_pilot_without_pilot_email_is_not_a_ready_provider(client, make_user, headers_for, capture, monkeypatch):
    """In the pilot the transport drops every email, so the switch must not promise delivery."""
    monkeypatch.setattr(settings, "pilot_mode_enabled", True)
    monkeypatch.setattr(settings, "pilot_email_delivery_enabled", False)
    salon = _salon(client, make_user, headers_for, "Pilot")
    current = _ok(client.get("/businesses/me/notification-settings", headers=salon["headers"]), "settings")
    assert (current["provider_ready"], current["available_channels"]) == (False, [])
    response = client.put("/businesses/me/notification-settings", json={"is_enabled": True}, headers=salon["headers"])
    assert response.status_code == 409


def test_staff_cannot_read_or_change_notifications(client, db, make_user, headers_for, capture):
    salon = _salon(client, make_user, headers_for, "Staffed")
    staff = headers_for(db.get(User, salon["employee_id"]))
    assert client.get("/businesses/me/notification-settings", headers=staff).status_code == 403
    assert client.put("/businesses/me/notification-settings", json={"is_enabled": True}, headers=staff).status_code == 403
    assert client.get("/businesses/me/notification-deliveries", headers=staff).status_code == 403


def test_test_button_only_captures(client, make_user, headers_for, capture, no_network):
    salon = _salon(client, make_user, headers_for, "Tester")
    result = _ok(client.post("/businesses/me/notification-settings/test", headers=salon["headers"]), "test")
    assert (result["status"], result["provider"]) == ("captured", "capture")
    assert "@" in result["recipient"] and result["recipient"].count("*") >= 1


# MSG-02/03/06/11/12: the whole guest journey through the real job

@pytest.mark.parametrize("language,subject", [
    ("en", "Booking received"), ("ru", "Запись создана"), ("cz", "Rezervace vytvořena"),
])
def test_booking_mail_is_captured_in_the_guest_language_with_a_working_link(
    client, db, make_user, headers_for, capture, no_network, language, subject
):
    salon = _salon(client, make_user, headers_for, f"Lang{language}")
    _enable(client, salon)
    booked, visit = _booked_visit(client, db, salon, language=language)
    # the same web request again: same booking, no second message (MSG-02)
    start = visit.date_start.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    again = client.post(f"/public/booking/{salon['slug']}/bookings", json=_body(salon, start, "msg-key-0001"),
                        headers={"Accept-Language": language})
    assert again.status_code == 200, again.text
    created = _rows(db, visit.id, notification_events.KIND_CREATED)
    assert len(created) == 1 and created[0].language == language
    assert len(_rows(db, visit.id, notification_events.KIND_REMINDER)) == 1

    stats = _deliver()
    assert stats["captured"] == 1 and stats["sent"] == 0
    message = [m for m in capture.messages() if m.recipient == "guest@example.com"][-1]
    assert message.subject == subject
    db.expire_all()
    assert db.get(NotificationOutbox, created[0].id).status == "captured"

    # the link in the mail opens the booking; only its hash is stored (MSG-06)
    raw = _token_from(message)
    summary = _ok(client.get("/public/booking-management", headers={"X-Booking-Token": raw}), "manage")
    assert summary["status"] == "planned"
    assert raw not in json.dumps(created[0].payload)
    assert all(raw != row.token_hash for row in db.query(BookingManageToken).all())
    # the salon's wall clock, not UTC
    local = visit.date_start.replace(tzinfo=timezone.utc).astimezone(
        __import__("zoneinfo").ZoneInfo("Europe/Prague"))
    assert local.strftime("%d.%m.%Y %H:%M") in message.text


def test_names_are_escaped_in_html(client, db, make_user, headers_for, capture, no_network):
    salon = _salon(client, make_user, headers_for, "Escape")
    _enable(client, salon)
    _booked_visit(client, db, salon, name='<b>Eve</b> & "Co"')
    _deliver()
    message = capture.messages()[-1]
    assert "<b>Eve</b>" in message.text
    assert "<b>" not in message.html and "&lt;b&gt;Eve&lt;/b&gt; &amp; &quot;Co&quot;" in message.html
    assert message.html.count("<a href=") == 1


def test_move_and_cancel_leave_no_stale_reminder(client, db, make_user, headers_for, capture, no_network):
    salon = _salon(client, make_user, headers_for, "Mover")
    _enable(client, salon)
    _booked_visit(client, db, salon)
    _deliver()
    raw = _token_from(capture.messages()[-1])
    visit_id = db.query(Visit.id).filter(Visit.business_id == salon["business"]["id"]).scalar()
    old = _rows(db, visit_id, notification_events.KIND_REMINDER)[0]

    target, _day = _local_utc(days_ahead=6, hour=11)
    _ok(client.post("/public/booking-management/reschedule", json={"start": target},
                    headers={"X-Booking-Token": raw}), "move")
    reminders = _rows(db, visit_id, notification_events.KIND_REMINDER)
    assert [r.status for r in reminders] == ["cancelled", "pending"]
    assert reminders[0].id == old.id and reminders[1].event_key != old.event_key
    assert len(_rows(db, visit_id, notification_events.KIND_CHANGED)) == 1
    # a second identical move is a no-op, not a second email
    _ok(client.post("/public/booking-management/reschedule", json={"start": target},
                    headers={"X-Booking-Token": raw}), "move again")
    assert len(_rows(db, visit_id, notification_events.KIND_CHANGED)) == 1

    _ok(client.post("/public/booking-management/cancel", headers={"X-Booking-Token": raw}), "cancel")
    assert {r.status for r in _rows(db, visit_id, notification_events.KIND_REMINDER)} == {"cancelled"}
    assert len(_rows(db, visit_id, notification_events.KIND_CANCELLED)) == 1

    # the job sweeps once more: nothing is revived for a cancelled visit
    _deliver()
    subjects = [m.subject for m in capture.messages()]
    assert subjects == ["Booking received", "Your booking was moved", "Your booking was cancelled"]
    assert {r.status for r in _rows(db, visit_id, notification_events.KIND_REMINDER)} == {"cancelled"}


def test_due_reminder_is_captured_at_its_local_time(client, db, make_user, headers_for, capture, no_network):
    salon = _salon(client, make_user, headers_for, "Remind")
    _enable(client, salon, reminder_time="18:30")
    _booked_visit(client, db, salon, days=3)
    visit_id = db.query(Visit.id).filter(Visit.business_id == salon["business"]["id"]).scalar()
    reminder = _rows(db, visit_id, notification_events.KIND_REMINDER)[0]
    from zoneinfo import ZoneInfo
    local = reminder.scheduled_at.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("Europe/Prague"))
    assert local.strftime("%H:%M") == "18:30"
    # changing the time re-plans the pending reminder
    _enable(client, salon, reminder_time="08:15")
    reminder = [r for r in _rows(db, visit_id, notification_events.KIND_REMINDER) if r.status == "pending"][0]
    local = reminder.scheduled_at.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("Europe/Prague"))
    assert local.strftime("%H:%M") == "08:15"
    _deliver()  # the booking mail now; the reminder is not due yet
    assert [m.subject for m in capture.messages()] == ["Booking received"]
    _deliver(now=reminder.scheduled_at + timedelta(minutes=1))
    assert [m.subject for m in capture.messages()] == ["Booking received", "Appointment reminder"]


def test_failed_booking_rolls_back_its_messages(client, db, make_user, headers_for, capture, monkeypatch):
    salon = _salon(client, make_user, headers_for, "Rollback")
    _enable(client, salon)

    def boom(*args, **kwargs):
        raise RuntimeError("crash after the booking mail was queued")

    monkeypatch.setattr(notification_events, "ensure_reminder", boom)
    start, _day = _local_utc()
    with pytest.raises(RuntimeError):
        _book(client, salon, _body(salon, start, "msg-crash-0001"))
    db.expire_all()
    assert db.query(Visit).filter(Visit.business_id == salon["business"]["id"]).count() == 0
    assert db.query(NotificationOutbox).filter(
        NotificationOutbox.business_id == salon["business"]["id"]).count() == 0


# MSG-04: the reminder hour does not move across a DST switch

@pytest.mark.parametrize("visit_local,reminder_utc", [
    # the night before spring forward is still winter time
    ("2027-03-28T10:00:00+02:00", "2027-03-27T09:00:00"),
    # the day after spring forward: summer time
    ("2027-03-29T10:00:00+02:00", "2027-03-28T08:00:00"),
    # the day after fall back: winter time again
    ("2027-11-01T10:00:00+01:00", "2027-10-31T09:00:00"),
])
def test_reminder_keeps_the_local_hour_across_dst(visit_local, reminder_utc):
    start = datetime.fromisoformat(visit_local).astimezone(timezone.utc).replace(tzinfo=None)
    visit = Visit(id=1, date_start=start)
    business = Business(timezone="Europe/Prague")
    assert notification_events.reminder_at(visit, business, "10:00") == datetime.fromisoformat(reminder_utc)


# MSG-05: two workers never send one message; a known provider id is never resent

def test_two_workers_do_not_send_the_same_message(db, client, make_user, headers_for, capture):
    salon = _salon(client, make_user, headers_for, "Twins")
    _enable(client, salon)
    business_id = salon["business"]["id"]
    for n in range(6):
        enqueue_notification(db, event_key=f"twins:{n}", kind="booking_created", business_id=business_id,
                             recipient=f"guest{n}@example.com")
    db.commit()
    sent: list[int] = []
    lock = threading.Lock()
    start = threading.Barrier(2)

    def send(row):
        with lock:
            sent.append(row.id)
        clock.sleep(0.05)
        return DeliveryResult("sent", "smtp")

    def worker():
        session = SessionLocal()
        try:
            start.wait()
            notification_worker.run_once(session, send)
        finally:
            session.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(sent) == sorted(set(sent)) and len(sent) == 6
    db.expire_all()
    assert {row.status for row in db.query(NotificationOutbox).filter_by(business_id=business_id)} == {"sent"}


def test_known_provider_id_is_finished_without_a_second_send(db, client, make_user, headers_for, capture):
    salon = _salon(client, make_user, headers_for, "Known")
    _enable(client, salon)
    row = enqueue_notification(db, event_key="known:1", kind="booking_created",
                               business_id=salon["business"]["id"], recipient="guest@example.com")
    # a worker died right after the provider accepted the message
    row.status, row.attempts = "processing", 1
    row.provider, row.provider_message_id = "twilio", "SM-accepted"
    row.locked_until = utcnow() - timedelta(minutes=1)
    db.commit()
    sent = []
    notification_worker.run_once(db, lambda r: sent.append(r.id) or DeliveryResult("sent", "twilio"))
    assert sent == []
    db.expire_all()
    assert db.get(NotificationOutbox, row.id).status == "sent"


# MSG-08/10: the owner's log is theirs only, masked, and retry never resends a delivery

def test_delivery_log_is_private_masked_and_retry_is_guarded(client, db, make_user, headers_for, capture):
    salon = _salon(client, make_user, headers_for, "Logged")
    other = _salon(client, make_user, headers_for, "Neighbour")
    _enable(client, salon)
    _enable(client, other)
    _booked_visit(client, db, salon)
    _deliver()

    response = client.get("/businesses/me/notification-deliveries", headers=salon["headers"])
    log = _ok(response, "log")
    # the booking mail went out; its reminder waits for the day before
    assert sorted((i["kind"], i["status"]) for i in log["items"]) == [
        ("booking_created", "captured"), ("booking_reminder", "pending")]
    item = next(i for i in log["items"] if i["kind"] == "booking_created")
    assert item["status"] == "captured" and item["retryable"] is False
    assert item["recipient"] == "g***@example.com"
    assert "token" not in response.text and "payload" not in response.text and "event_key" not in response.text
    assert _ok(client.get("/businesses/me/notification-deliveries", headers=other["headers"]), "other")["total"] == 0
    assert client.post(f"/businesses/me/notification-deliveries/{item['id']}/retry",
                       headers=other["headers"]).status_code == 404
    assert client.post(f"/businesses/me/notification-deliveries/{item['id']}/retry",
                       headers=salon["headers"]).status_code == 409

    row = db.get(NotificationOutbox, item["id"])
    row.status, row.last_error = "failed", "smtp_error:SMTPServerDisconnected"
    db.commit()
    retried = _ok(client.post(f"/businesses/me/notification-deliveries/{item['id']}/retry",
                              headers=salon["headers"]), "retry")
    assert (retried["status"], retried["attempts"]) == ("pending", 0)

    # a delivery the provider already accepted is never offered again
    row = db.get(NotificationOutbox, item["id"])
    db.refresh(row)
    row.status, row.provider_message_id = "failed", "SM-accepted"
    db.commit()
    assert client.post(f"/businesses/me/notification-deliveries/{item['id']}/retry",
                       headers=salon["headers"]).status_code == 409

    # with the switch off a retry would only be cancelled again
    row.provider_message_id = None
    db.commit()
    _ok(client.put("/businesses/me/notification-settings", json={"is_enabled": False},
                   headers=salon["headers"]), "disable")
    assert client.post(f"/businesses/me/notification-deliveries/{item['id']}/retry",
                       headers=salon["headers"]).status_code == 409


# MSG-11: the job says out loud when nothing is really delivered

def test_job_warns_about_capture(caplog, capture):
    with caplog.at_level(logging.WARNING):
        notification_jobs.sync_and_deliver()
    assert "capture transport is active" in caplog.text


def test_job_without_provider_warns_and_queued_mail_is_not_reported_sent(
    client, db, make_user, headers_for, capture, monkeypatch, caplog, no_network
):
    salon = _salon(client, make_user, headers_for, "Unplugged")
    _enable(client, salon)
    _booked_visit(client, db, salon)
    # the provider is gone before the job runs
    monkeypatch.setattr(settings, "smtp_host", "")
    monkeypatch.setattr(settings, "notification_transport", "")
    monkeypatch.setattr(notification_transport, "_transport", None)
    with caplog.at_level(logging.WARNING):
        stats = notification_jobs.sync_and_deliver()["delivery"]
    assert "email provider is not configured" in caplog.text
    assert stats["sent"] == 0
    db.expire_all()
    statuses = {r.status for r in db.query(NotificationOutbox).filter_by(business_id=salon["business"]["id"])}
    assert "sent" not in statuses and "captured" not in statuses
