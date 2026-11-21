"""POST /visits/reminders/send goes through the same gate as every other client message.

SMS and WhatsApp are off for the whole installation, so asking for them is refused instead of
quietly reaching Twilio. Email still needs a ready provider and the salon's own switch.
"""
from datetime import timedelta

import pytest

from app.api.core import notify, notification_transport
from app.api.core.clock import utcnow
from app.api.core.config import settings
from tests.tenant_world import build_tenant


@pytest.fixture
def wire(monkeypatch):
    """Anything that reaches the transport lands here, whatever the channel."""
    sent = []

    class Spy:
        name = "spy"

        def send(self, message):
            sent.append(message)
            return notification_transport.DeliveryResult("sent", "spy", "id-1")

    monkeypatch.setattr(notify, "get_notification_transport", lambda: Spy())
    return sent


@pytest.fixture
def salon(client, make_user, headers_for):
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070061")
    soon = (utcnow() + timedelta(hours=5)).replace(microsecond=0)
    r = client.post("/visits/", headers=a["headers"], json={
        "client_id": a["client"]["id"], "specialist_id": a["owner"].id,
        "date_start": soon.isoformat(), "date_end": (soon + timedelta(hours=1)).isoformat()})
    assert r.status_code == 200, r.text
    return a


def _provider(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.invalid")
    monkeypatch.setattr(settings, "smtp_from", "notifications@example.com")


@pytest.mark.parametrize("channel", ["sms", "whatsapp", "pigeon"])
def test_channels_that_are_not_ready_are_refused(client, salon, wire, monkeypatch, channel):
    _provider(monkeypatch)
    client.put("/businesses/me/notification-settings", json={"is_enabled": True}, headers=salon["headers"])
    r = client.post("/visits/reminders/send", params={"channel": channel}, headers=salon["headers"])
    assert r.status_code == 409, r.text
    assert wire == []


def test_email_without_a_provider_is_refused(client, salon, wire):
    r = client.post("/visits/reminders/send", params={"channel": "email"}, headers=salon["headers"])
    assert r.status_code == 409
    assert wire == []


def test_email_skips_a_salon_that_turned_notifications_off(client, salon, wire, monkeypatch):
    _provider(monkeypatch)
    r = client.post("/visits/reminders/send", params={"channel": "email"}, headers=salon["headers"])
    assert r.status_code == 200 and r.json()["sent"] == 0
    assert wire == []


def test_email_goes_out_when_the_salon_has_it_on(client, salon, wire, monkeypatch):
    _provider(monkeypatch)
    client.put("/businesses/me/notification-settings", json={"is_enabled": True}, headers=salon["headers"])
    r = client.post("/visits/reminders/send", params={"channel": "email"}, headers=salon["headers"])
    assert r.status_code == 200 and r.json()["sent"] == 1
    assert [m.recipient for m in wire] == ["alpha.client@example.com"]


def test_log_channel_stays_a_dry_run(client, salon, wire):
    r = client.post("/visits/reminders/send", params={"channel": "log"}, headers=salon["headers"])
    assert r.status_code == 200 and r.json()["sent"] == 0
    assert wire == []


def test_low_level_sms_helpers_refuse_while_sms_is_off(wire):
    assert notify._send_sms("+420777000111", "hi") is False
    assert notify.send_whatsapp_notification("+420777000111", "hi") is False
    assert wire == []
