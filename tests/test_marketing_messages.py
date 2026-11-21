"""Birthday greetings and thank-you notes are marketing: salon switch + client consent, with an opt-out.

Nothing goes out by default. The owner turns each type on separately, and even then only
clients who explicitly agreed to marketing from that salon get it. Each message carries the
same signed unsubscribe link campaigns use.
"""
from datetime import date, timedelta
from urllib.parse import unquote

import pytest

from app.api.core import campaign_service, notify, notification_tasks
from app.api.core.clock import utcnow
from app.db.models.marketing_consent import MarketingConsent
from tests.tenant_world import build_tenant


@pytest.fixture
def mail(monkeypatch):
    from app.api.core.config import settings

    monkeypatch.setattr(settings, "smtp_host", "smtp.invalid")
    monkeypatch.setattr(settings, "smtp_from", "notifications@example.com")
    monkeypatch.setattr(settings, "frontend_url", "http://localhost:3170")
    sent = []
    monkeypatch.setattr(notify, "_send_email", lambda to, subject, body: sent.append((to, subject, body)) or True)
    return sent


def _ok(r):
    assert r.status_code in (200, 201), r.text
    return r.json()


def _switch(client, tenant, **flags):
    return _ok(client.put("/businesses/me/notification-settings", json=flags, headers=tenant["headers"]))


def _consent(db, tenant, client_id=None):
    consent = campaign_service.record_consent(
        db, business_id=tenant["business"]["id"], client_id=client_id or tenant["client"]["id"], source="test",
    )
    db.commit()
    return consent


def _birthday_today(client, tenant):
    r = client.put(f"/clients/{tenant['client']['id']}", headers=tenant["headers"],
                   json={"birth_date": date.today().replace(year=1992).isoformat()})
    assert r.status_code == 200, r.text


def _finished_visit(client, tenant):
    done = (utcnow() - timedelta(hours=25, minutes=30)).replace(microsecond=0)
    body = {"client_id": tenant["client"]["id"], "specialist_id": tenant["owner"].id,
            "date_start": done.isoformat(), "date_end": (done + timedelta(hours=1)).isoformat(), "price": 100}
    visit = _ok(client.post("/visits/", json=body, headers=tenant["headers"]))
    _ok(client.put(f"/visits/{visit['id']}", json={"status": "completed", "payment_method": "cash"},
                   headers=tenant["headers"]))


@pytest.fixture
def alpha(client, make_user, headers_for, mail):
    return build_tenant(client, make_user, headers_for, "Alpha", "+42070051")


def test_switches_default_off_for_marketing_and_on_for_crm_messages(client, alpha):
    got = _ok(client.get("/businesses/me/notification-settings", headers=alpha["headers"]))
    assert got["birthday_greetings_enabled"] is False
    assert got["visit_followups_enabled"] is False
    assert got["crm_visit_messages_enabled"] is True
    r = client.put("/businesses/me/notification-settings", json={"birthday_greetings_enabled": None},
                   headers=alpha["headers"])
    assert r.status_code == 422


def test_no_greeting_when_the_salon_did_not_turn_it_on(client, db, alpha, mail):
    _switch(client, alpha, is_enabled=True)
    _consent(db, alpha)
    _birthday_today(client, alpha)
    _finished_visit(client, alpha)

    assert notification_tasks.send_birthday_greetings("email") == 0
    assert notification_tasks.send_visit_followups("email") == 0
    assert mail == []


def test_no_greeting_without_marketing_consent(client, db, alpha, mail):
    _switch(client, alpha, is_enabled=True, birthday_greetings_enabled=True, visit_followups_enabled=True)
    _birthday_today(client, alpha)
    _finished_visit(client, alpha)

    assert notification_tasks.send_birthday_greetings("email") == 0
    assert notification_tasks.send_visit_followups("email") == 0
    assert mail == []


def test_no_greeting_when_notifications_are_off_overall(client, db, alpha, mail):
    _switch(client, alpha, is_enabled=True, birthday_greetings_enabled=True)
    _switch(client, alpha, is_enabled=False)
    _consent(db, alpha)
    _birthday_today(client, alpha)

    assert notification_tasks.send_birthday_greetings("email") == 0


def test_revoked_consent_stops_greetings(client, db, alpha, mail):
    _switch(client, alpha, is_enabled=True, birthday_greetings_enabled=True)
    consent = _consent(db, alpha)
    campaign_service.revoke_consent(db, consent, source="test")
    db.commit()
    _birthday_today(client, alpha)

    assert notification_tasks.send_birthday_greetings("email") == 0


def test_greeting_with_consent_carries_a_working_unsubscribe_link(client, db, alpha, mail):
    _switch(client, alpha, is_enabled=True, birthday_greetings_enabled=True)
    consent = _consent(db, alpha)
    _birthday_today(client, alpha)

    assert notification_tasks.send_birthday_greetings("email") == 1
    assert notification_tasks.send_birthday_greetings("email") == 0
    to, _subject, body = mail[0]
    assert to == "alpha.client@example.com"
    assert "http://localhost:3170/unsubscribe#token=" in body
    token = unquote(body.split("/unsubscribe#token=", 1)[1].split()[0])

    preview = _ok(client.get("/public/campaigns/unsubscribe", params={"token": token}))
    assert preview["business_name"] == "Alpha Salon" and preview["status"] == "subscribed"
    assert _ok(client.post("/public/campaigns/unsubscribe", json={"token": token}))["status"] == "unsubscribed"
    db.expire_all()
    row = db.get(MarketingConsent, consent.id)
    assert row.status == "unsubscribed" and row.revoked_campaign_id is None


def test_followup_with_consent_goes_once_with_unsubscribe_link(client, db, alpha, mail):
    _switch(client, alpha, is_enabled=True, visit_followups_enabled=True)
    _consent(db, alpha)
    _finished_visit(client, alpha)

    assert notification_tasks.send_visit_followups("email") == 1
    assert notification_tasks.send_visit_followups("email") == 0
    assert "/unsubscribe#token=" in mail[0][2]


def test_consent_in_one_salon_does_not_open_another(client, db, make_user, headers_for, alpha, mail):
    beta = build_tenant(client, make_user, headers_for, "Beta", "+42070052")
    # both salons have a birthday client and the switch on, but only Beta's client agreed
    for tenant in (alpha, beta):
        _switch(client, tenant, is_enabled=True, birthday_greetings_enabled=True)
    _consent(db, beta, client_id=beta["client"]["id"])
    _birthday_today(client, alpha)
    _birthday_today(client, beta)

    assert notification_tasks.send_birthday_greetings("email") == 1
    assert [to for to, _s, _b in mail] == ["beta.client@example.com"]


def test_sms_greetings_stay_off_while_sms_is_not_ready(client, db, alpha, monkeypatch):
    texts = []
    monkeypatch.setattr(notify, "get_notification_transport",
                        lambda: type("T", (), {"send": lambda self, m: texts.append(m)})())
    _switch(client, alpha, is_enabled=True, birthday_greetings_enabled=True)
    _consent(db, alpha)
    _birthday_today(client, alpha)

    assert notification_tasks.send_birthday_greetings("sms") == 0
    assert notification_tasks.send_birthday_greetings("whatsapp") == 0
    assert texts == []
