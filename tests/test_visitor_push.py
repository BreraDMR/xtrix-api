"""Visit reminders reach the visitor's phone only while the device's session is alive."""
from datetime import timedelta

import pytest
import requests
from pydantic import SecretStr

from app.api.core import notification_events, notification_push, notification_scheduler
from app.api.core import notification_templates, notification_transport, notification_worker, visitor_sessions
from app.api.core.clock import utcnow
from app.api.core.config import settings
from app.api.core.notification_transport import DeliveryResult, OutboundMessage
from app.db.models.booking_manage_token import BookingManageToken
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.visit import Visit
from app.db.models.visitor_account import VisitorAccount
from app.db.models.visitor_push_token import VisitorPushToken
from tests import test_visitor_history as base

world = base.world
PHONE = "ExponentPushToken[phone-one-AAAAAAAAAA]"
TABLET = "ExponentPushToken[tablet-one-BBBBBBBBB]"
STRANGER = "ExponentPushToken[stranger-CCCCCCCCCC]"


@pytest.fixture
def push(db, world, monkeypatch):
    monkeypatch.setattr(settings, "push_notifications_enabled", True)
    one = visitor_sessions.create_session(db, db.get(VisitorAccount, world["one"]))[1]
    two = visitor_sessions.create_session(db, db.get(VisitorAccount, world["two"]))[1]
    base.grant(db, world)  # account one sees every visit of the shared card in salon A
    start = utcnow().replace(microsecond=0) + timedelta(days=3)
    visit = base.add_visit(db, world, start=start, service_name="Haircut")
    db.commit()
    return {**world, "session_one": one, "session_two": two, "visit": visit}


def register(client, session, token, platform="ios", language="cz"):
    return client.put("/visitor/push-tokens", json={"token": token, "platform": platform},
                      headers={"X-Visitor-Token": session, "Accept-Language": language})


def push_rows(db, visit_id, status=None):
    query = db.query(NotificationOutbox).filter(NotificationOutbox.visit_id == visit_id,
                                                NotificationOutbox.channel == "push")
    if status:
        query = query.filter(NotificationOutbox.status == status)
    return query.order_by(NotificationOutbox.id).all()


def test_registering_a_device_needs_a_visitor_session(client, push):
    response = client.put("/visitor/push-tokens", json={"token": PHONE, "platform": "ios"})
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("token", ["", "plain-token", "ExponentPushToken[]", "ExponentPushToken[a b]",
                                   "ExponentPushToken[" + "a" * 300 + "]"])
def test_only_expo_tokens_are_accepted(client, push, token):
    assert register(client, push["session_one"], token).status_code == 422


def test_register_is_idempotent_and_a_device_follows_the_last_login(client, db, push):
    assert register(client, push["session_one"], PHONE).status_code == 204
    assert register(client, push["session_one"], PHONE, language="en").status_code == 204
    rows = db.query(VisitorPushToken).all()
    assert [(row.token, row.platform, row.language) for row in rows] == [(PHONE, "ios", "en")]
    # someone else signs in on the same phone: the reminders follow them, not the old owner
    assert register(client, push["session_two"], PHONE).status_code == 204
    db.expire_all()
    row = db.query(VisitorPushToken).one()
    assert db.get(VisitorAccount, push["two"]).id == row.session.account_id


def test_unregister_removes_only_own_device(client, db, push):
    register(client, push["session_one"], PHONE)
    register(client, push["session_two"], STRANGER)
    response = client.delete(f"/visitor/push-tokens/{STRANGER}", headers={"X-Visitor-Token": push["session_one"]})
    assert response.status_code == 204
    response = client.delete(f"/visitor/push-tokens/{PHONE}", headers={"X-Visitor-Token": push["session_one"]})
    assert response.status_code == 204
    db.expire_all()
    assert [row.token for row in db.query(VisitorPushToken).all()] == [STRANGER]


def test_reminder_is_queued_for_every_device_that_can_see_the_visit(client, db, push):
    register(client, push["session_one"], PHONE)
    register(client, push["session_one"], TABLET, platform="android", language="ru")
    register(client, push["session_two"], STRANGER)  # no grant: never learns about the visit
    notification_scheduler.sync_reminders(db)
    rows = push_rows(db, push["visit"], "pending")
    visit = db.get(Visit, push["visit"])
    assert [(row.recipient, row.language) for row in rows] == [(PHONE, "cz"), (TABLET, "ru")]
    assert {row.kind for row in rows} == {notification_events.KIND_REMINDER}
    assert {row.scheduled_at for row in rows} == {notification_events.reminder_at(visit, visit.business)}
    # a second sweep changes nothing
    notification_scheduler.sync_reminders(db)
    assert len(push_rows(db, push["visit"])) == 2


def test_push_does_not_depend_on_the_salon_email_switch(client, db, push):
    # the salon never turned on email reminders, but the visitor allowed notifications on the phone
    register(client, push["session_one"], PHONE)
    notification_scheduler.sync_reminders(db)
    assert len(push_rows(db, push["visit"], "pending")) == 1
    assert db.query(NotificationOutbox).filter(NotificationOutbox.channel == "email").count() == 0


def test_nothing_is_queued_while_push_is_switched_off(client, db, push, monkeypatch):
    register(client, push["session_one"], PHONE)
    notification_scheduler.sync_reminders(db)
    monkeypatch.setattr(settings, "push_notifications_enabled", False)
    notification_scheduler.sync_reminders(db)
    assert [row.status for row in push_rows(db, push["visit"])] == ["cancelled"]


def test_moving_the_visit_replaces_the_phone_reminder(client, db, push):
    register(client, push["session_one"], PHONE)
    notification_scheduler.sync_reminders(db)
    visit = db.get(Visit, push["visit"])
    visit.date_start = visit.date_start + timedelta(days=1)
    visit.date_end = visit.date_end + timedelta(days=1)
    db.commit()
    notification_scheduler.sync_reminders(db)
    rows = push_rows(db, push["visit"])
    assert [row.status for row in rows] == ["cancelled", "pending"]
    assert rows[1].event_key.startswith(notification_events.reminder_event_key(visit) + ":push:")


@pytest.mark.parametrize("gone", ["logout", "unregister", "grant"])
def test_device_that_lost_access_is_not_sent_to(client, db, push, gone):
    register(client, push["session_one"], PHONE)
    notification_scheduler.sync_reminders(db)
    row = push_rows(db, push["visit"], "pending")[0]
    headers = {"X-Visitor-Token": push["session_one"]}
    if gone == "logout":
        assert client.post("/visitor/auth/logout", headers=headers).status_code == 204
    elif gone == "unregister":
        assert client.delete(f"/visitor/push-tokens/{PHONE}", headers=headers).status_code == 204
    else:
        from app.db.models.visitor_access import VisitorGrant
        db.query(VisitorGrant).update({VisitorGrant.revoked_at: utcnow()})
        db.commit()
    sent = []
    notification_worker.run_once(db, lambda message: sent.append(message.id) or DeliveryResult("sent", "expo"),
                                 now=row.scheduled_at + timedelta(minutes=1))
    assert sent == []
    db.expire_all()
    assert db.get(NotificationOutbox, row.id).status == "cancelled"


def test_phone_text_is_short_localized_and_has_no_manage_link(client, db, push):
    register(client, push["session_one"], PHONE)
    notification_scheduler.sync_reminders(db)
    row = push_rows(db, push["visit"], "pending")[0]
    message = notification_templates.render_outbox_notification(db, row)
    assert message.channel == "push" and message.recipient == PHONE
    assert message.subject == "Připomínka návštěvy"
    assert "Haircut" in message.text and "Salon A" in message.text
    assert message.data == {"visit_id": push["visit"]}
    # no link means no live manage token sits in a notification log on the phone
    assert db.query(BookingManageToken).count() == 0
    assert "private" not in message.text


class _Response:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


def test_expo_transport_reports_the_ticket(monkeypatch):
    calls = []

    def post(url, json, headers, timeout):
        calls.append((url, json, headers))
        return _Response(200, {"data": [{"status": "ok", "id": "ticket-1"}]})

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(settings, "expo_push_access_token", SecretStr("secret-expo"))
    result = notification_transport.ExpoPushTransport().send(
        OutboundMessage(channel="push", recipient=PHONE, subject="Title", text="Body", data={"visit_id": 7}))
    assert result == DeliveryResult("sent", "expo", provider_message_id="ticket-1")
    url, body, headers = calls[0]
    assert url == "https://exp.host/--/api/v2/push/send"
    assert body == [{"to": PHONE, "title": "Title", "body": "Body", "data": {"visit_id": 7}, "sound": "default"}]
    assert headers["Authorization"] == "Bearer secret-expo"


@pytest.mark.parametrize("response,expected", [
    (_Response(200, {"data": [{"status": "error", "details": {"error": "DeviceNotRegistered"}}]}),
     DeliveryResult("skipped", "expo", error="push_device_not_registered")),
    (_Response(200, {"data": [{"status": "error", "details": {"error": "MessageRateExceeded"}}]}),
     DeliveryResult("failed", "expo", error="expo:MessageRateExceeded")),
    (_Response(503, {"errors": []}), DeliveryResult("failed", "expo", error="expo_http:503")),
])
def test_expo_transport_maps_errors(monkeypatch, response, expected):
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: response)
    result = notification_transport.ExpoPushTransport().send(
        OutboundMessage(channel="push", recipient=PHONE, subject="T", text="B"))
    assert result == expected


def test_dead_device_is_forgotten_and_not_retried(client, db, push, monkeypatch):
    register(client, push["session_one"], PHONE)
    notification_scheduler.sync_reminders(db)
    row = push_rows(db, push["visit"], "pending")[0]

    class Dead:
        name = "live"

        def send(self, message):
            return DeliveryResult("skipped", "expo", error="push_device_not_registered")

    monkeypatch.setattr(notification_templates, "get_notification_transport", lambda: Dead())
    stats = notification_worker.run_once(db, lambda message: notification_templates.send_outbox_message(db, message),
                                         now=row.scheduled_at + timedelta(minutes=1))
    assert stats["skipped"] == 1
    db.expire_all()
    assert db.query(VisitorPushToken).count() == 0
    assert db.get(NotificationOutbox, row.id).status == "skipped"


def test_live_transport_never_sends_push_in_pilot_mode(monkeypatch):
    monkeypatch.setattr(settings, "pilot_mode_enabled", True)
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: pytest.fail("network call"))
    result = notification_transport.LiveNotificationTransport().send(
        OutboundMessage(channel="push", recipient=PHONE, subject="T", text="B"))
    assert result.status == "skipped"


@pytest.mark.parametrize("how", ["delete_account", "unregister", "logout", "logout_all"])
def test_forgotten_device_leaves_no_token_in_the_queue(client, db, push, how):
    register(client, push["session_one"], PHONE)
    register(client, push["session_two"], STRANGER)
    notification_scheduler.sync_reminders(db)
    sent = push_rows(db, push["visit"])[0]
    sent.status = "sent"  # an earlier reminder already went out
    db.add(NotificationOutbox(business_id=push["a"], visit_id=push["visit"], event_key="older:push:1",
                              kind=notification_events.KIND_REMINDER, channel="push", recipient=PHONE,
                              status="pending", payload={}))
    db.commit()
    headers = {"X-Visitor-Token": push["session_one"]}
    if how == "delete_account":
        assert client.delete("/visitor/me", headers=headers).status_code == 204
    elif how in ("logout", "logout_all"):
        path = "/visitor/auth/logout" if how == "logout" else "/visitor/auth/logout-all"
        assert client.post(path, headers=headers).status_code == 204
    else:
        assert client.delete(f"/visitor/push-tokens/{PHONE}", headers=headers).status_code == 204
    db.expire_all()
    assert [row.token for row in db.query(VisitorPushToken).all()] == [STRANGER]
    rows = db.query(NotificationOutbox).filter(NotificationOutbox.channel == notification_push.CHANNEL)
    assert rows.filter(NotificationOutbox.recipient == PHONE).count() == 0
    assert sorted(row.status for row in rows.filter(NotificationOutbox.recipient == "deleted")) == ["cancelled", "sent"]
