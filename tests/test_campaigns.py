"""Campaign consent, segments, frozen confirmation, outbox dispatch and unsubscribe."""
import re
from datetime import datetime, time, timedelta, timezone
from urllib.parse import quote, unquote
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func

from app.api.core import campaign_service, notification_templates, notification_worker
from app.api.core.clock import utcnow
from app.api.core.notification_transport import DeliveryResult
from app.db.models.campaign import Campaign
from app.db.models.client import Client
from app.db.models.client_business_relation import ClientBusinessRelation
from app.db.models.marketing_consent import MarketingConsent
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.visit import Visit
from tests.tenant_world import build_tenant

ZONE = ZoneInfo("Europe/Prague")


def _ok(response, what):
    assert response.status_code in (200, 201), f"{what}: {response.status_code} {response.text}"
    return response.json()


def _enable_notifications(client, world):
    return _ok(
        client.put(
            "/businesses/me/notification-settings",
            json={"channel": "email", "is_enabled": True, "reminder_time": "10:00"},
            headers=world["headers"],
        ),
        "enable notifications",
    )


@pytest.fixture
def world(client, db, make_user, headers_for, monkeypatch):
    from app.api.core.config import settings

    monkeypatch.setattr(settings, "frontend_url", "http://localhost:3170")
    monkeypatch.setattr(settings, "smtp_host", "smtp.invalid")
    monkeypatch.setattr(settings, "smtp_from", "notifications@example.com")
    tenant = build_tenant(client, make_user, headers_for, "Alpha", "+42070031")
    _enable_notifications(client, tenant)
    return tenant


def _add_client(client, world, name, email):
    return _ok(
        client.post(
            "/clients/",
            json={"full_name": name, "contact_email": email},
            headers=world["headers"],
        ),
        f"client {name}",
    )


def _age_relation(db, business_id, client_id, days):
    relation = (
        db.query(ClientBusinessRelation)
        .filter(
            ClientBusinessRelation.business_id == business_id,
            ClientBusinessRelation.client_id == client_id,
        )
        .one()
    )
    relation.created_at = utcnow() - timedelta(days=days)
    db.commit()


def _consent(db, world, client_row, source="test"):
    consent = campaign_service.record_consent(
        db,
        business_id=world["business"]["id"],
        client_id=client_row["id"],
        source=source,
    )
    db.commit()
    return consent


def _completed_visit(db, world, client_id, days_ago=10, status="completed"):
    # offset per client: the exclusion constraint forbids two visits in one specialist slot
    start = utcnow() - timedelta(days=days_ago) + timedelta(hours=client_id)
    visit = Visit(
        client_id=client_id,
        specialist_id=world["owner"].id,
        service_id=world["service"]["id"],
        business_id=world["business"]["id"],
        date_start=start,
        date_end=start + timedelta(hours=1),
        price=50,
        status=status,
    )
    db.add(visit)
    db.commit()
    return visit


def _campaign_body(**overrides):
    body = {
        "name": "Come back",
        "subject": "We miss you, {name}",
        "body": "Hello {name}, it has been a while. Book with {business} again.",
        "segment_kind": "no_visits",
        "inactive_days": 60,
    }
    body.update(overrides)
    return body


def _create(client, world, **overrides):
    return _ok(
        client.post(
            "/businesses/me/campaigns",
            json=_campaign_body(**overrides),
            headers=world["headers"],
        ),
        "create campaign",
    )


def _confirm(client, world, campaign_id, preview=None):
    preview = preview or _preview(client, world, campaign_id)
    return client.post(
        f"/businesses/me/campaigns/{campaign_id}/confirm",
        json={"audience_hash": preview["audience_hash"], "content_hash": preview["content_hash"]},
        headers=world["headers"],
    )


def _preview(client, world, campaign_id):
    return _ok(
        client.get(
            f"/businesses/me/campaigns/{campaign_id}/preview",
            headers=world["headers"],
        ),
        "preview",
    )


def _outbox(db, campaign_id):
    return (
        db.query(NotificationOutbox)
        .filter(NotificationOutbox.campaign_id == campaign_id)
        .order_by(NotificationOutbox.id)
        .all()
    )


def _deliver_capture(db):
    return notification_worker.run_once(
        db,
        lambda row: (
            notification_templates.render_outbox_notification(db, row),
            DeliveryResult("captured", "capture"),
        )[1],
    )


def test_booking_consent_is_explicit_and_recorded_with_its_source(client, db, make_user, headers_for, monkeypatch):
    from app.api.core.config import settings

    monkeypatch.setattr(settings, "frontend_url", "http://localhost:3170")
    owner = make_user("business_owner", full_name="Booking Owner", email="booking.owner@example.com")
    headers = headers_for(owner)
    business = _ok(
        client.post(
            "/businesses/",
            json={"name": "Booking Salon", "business_type": "salon", "timezone": "Europe/Prague"},
            headers=headers,
        ),
        "business",
    )
    public = _ok(
        client.put(
            "/businesses/me/public-booking",
            json={"is_enabled": True},
            headers=headers,
        ),
        "public settings",
    )
    service = _ok(
        client.post(
            "/services/",
            json={
                "name": "Booking Cut",
                "price": 30,
                "duration_minutes": 30,
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
        "schedule",
    )
    def book(email, request_id, marketing, hour):
        slot = (
            datetime.combine(local_day, time(hour, 0), tzinfo=ZONE)
            .astimezone(timezone.utc)
            .replace(tzinfo=None)
        )
        return _ok(
            client.post(
                f"/public/booking/{public['slug']}/bookings",
                json={
                    "service_id": service["id"],
                    "staff_id": owner.id,
                    "start": slot.isoformat() + "Z",
                    "full_name": f"Guest {request_id}",
                    "email": email,
                    "processing_consent": True,
                    "marketing_consent": marketing,
                    "client_request_id": request_id,
                },
            ),
            f"booking {request_id}",
        )

    book("opted.in@example.com", "marketing-yes", True, 10)
    book("no.marketing@example.com", "marketing-no", False, 11)

    opted = db.query(Client).filter(Client.contact_email == "opted.in@example.com").one()
    silent = db.query(Client).filter(Client.contact_email == "no.marketing@example.com").one()
    consents = (
        db.query(MarketingConsent)
        .filter(MarketingConsent.business_id == business["id"])
        .all()
    )
    assert len(consents) == 1
    assert consents[0].client_id == opted.id
    assert consents[0].status == "subscribed"
    assert consents[0].source == "public_booking"
    assert consents[0].source_ref
    assert (
        db.query(MarketingConsent)
        .filter(MarketingConsent.client_id == silent.id)
        .count()
        == 0
    )


def test_segment_uses_completed_visits_consent_and_own_business(
    client, db, world, make_user, headers_for
):
    business_id = world["business"]["id"]
    old = _add_client(client, world, "Old Client", "old@example.com")
    recent = _add_client(client, world, "Recent Client", "recent@example.com")
    cancelled = _add_client(client, world, "Cancelled Client", "cancelled@example.com")
    unsubscribed = _add_client(client, world, "Unsubscribed Client", "unsub@example.com")
    no_consent = _add_client(client, world, "No Consent", "silent@example.com")
    # the API rejects a second card with the same mailbox, so insert the duplicate directly
    duplicate_row = Client(
        owner_id=world["owner"].id,
        full_name="Duplicate Mailbox",
        contact_email="old@example.com",
        language="en",
    )
    db.add(duplicate_row)
    db.flush()
    db.add(
        ClientBusinessRelation(
            client_id=duplicate_row.id, business_id=business_id
        )
    )
    db.commit()
    duplicate = {"id": duplicate_row.id}
    for row in (old, recent, cancelled, unsubscribed):
        _consent(db, world, row)
        _age_relation(db, business_id, row["id"], 200)
    _age_relation(db, business_id, no_consent["id"], 200)
    _age_relation(db, business_id, duplicate["id"], 100)
    _completed_visit(db, world, old["id"], days_ago=90)
    _completed_visit(db, world, recent["id"], days_ago=3)
    _completed_visit(db, world, cancelled["id"], days_ago=3, status="cancelled")
    consent = (
        db.query(MarketingConsent)
        .filter(
            MarketingConsent.business_id == business_id,
            MarketingConsent.client_id == unsubscribed["id"],
        )
        .one()
    )
    campaign_service.revoke_consent(db, consent, source="test")
    db.commit()

    recipients = campaign_service.segment_recipients(db, business_id, 60)
    by_email = {item["email"]: item for item in recipients}
    assert set(by_email) == {"old@example.com", "cancelled@example.com"}
    # two cards with one mailbox become one recipient; the card with the more recent
    # activity wins (a 90-day-old visit beats a 100-day-old relation)
    assert by_email["old@example.com"]["client_id"] == old["id"]
    assert len({item["email"].lower() for item in recipients}) == len(recipients)
    assert all(
        item["client_id"] not in {recent["id"], unsubscribed["id"], no_consent["id"]}
        for item in recipients
    )

    # another business never leaks into this audience, and its own segment stays separate
    other = build_tenant(client, make_user, headers_for, "Beta", "+42070032")
    stranger = _add_client(client, other, "Beta Stranger", "beta.stranger@example.com")
    _consent(db, other, stranger)
    _age_relation(db, other["business"]["id"], stranger["id"], 200)
    beta_recipients = campaign_service.segment_recipients(db, other["business"]["id"], 60)
    assert [item["email"] for item in beta_recipients] == ["beta.stranger@example.com"]
    assert "beta.stranger@example.com" not in by_email


def test_new_client_without_visits_is_not_immediately_targeted(client, db, world):
    fresh = _add_client(client, world, "Fresh Client", "fresh@example.com")
    _consent(db, world, fresh)
    assert campaign_service.segment_recipients(db, world["business"]["id"], 60) == []


def test_draft_preview_confirm_send_and_freeze(client, db, world):
    client_a = _add_client(client, world, "Lapsed A", "lapsed.a@example.com")
    client_b = _add_client(client, world, "Lapsed B", "lapsed.b@example.com")
    for row in (client_a, client_b):
        _consent(db, world, row)
        _age_relation(db, world["business"]["id"], row["id"], 200)
        _completed_visit(db, world, row["id"], days_ago=100)

    campaign = _create(client, world)
    assert campaign["status"] == "draft"
    preview = _preview(client, world, campaign["id"])
    assert preview["audience_source"] == "draft"
    assert preview["recipient_count"] == 2
    assert preview["requires_confirmation"] is True
    assert preview["send_ready"] is False
    assert all("@" in item["email"] and "***" in item["email"] for item in preview["recipients"])

    not_confirmed = client.post(
        f"/businesses/me/campaigns/{campaign['id']}/send", headers=world["headers"]
    )
    assert not_confirmed.status_code == 409
    assert not_confirmed.json()["error"]["message"] == "Confirm the campaign before sending"

    confirmed = _ok(
        _confirm(client, world, campaign["id"]),
        "confirm",
    )
    assert confirmed["status"] == "confirmed"
    assert confirmed["recipient_count"] == 2
    assert confirmed["audience_hash"] and confirmed["content_hash"]
    frozen = _preview(client, world, campaign["id"])
    assert frozen["audience_source"] == "frozen"
    assert frozen["send_ready"] is True

    # a new consenting client after confirmation does not enter the frozen audience
    late = _add_client(client, world, "Late Client", "late@example.com")
    _consent(db, world, late)
    _age_relation(db, world["business"]["id"], late["id"], 200)
    assert _preview(client, world, campaign["id"])["recipient_count"] == 2

    changed = _ok(
        client.patch(
            f"/businesses/me/campaigns/{campaign['id']}",
            json={"subject": "New subject"},
            headers=world["headers"],
        ),
        "edit confirmed",
    )
    assert changed["status"] == "draft"
    assert changed["audience_hash"] is None and changed["content_hash"] is None
    assert changed["recipient_count"] == 0
    assert _preview(client, world, campaign["id"])["requires_confirmation"] is True

    _ok(
        _confirm(client, world, campaign["id"]),
        "reconfirm",
    )
    sent = _ok(
        client.post(
            f"/businesses/me/campaigns/{campaign['id']}/send", headers=world["headers"]
        ),
        "send",
    )
    assert sent["status"] == "sending"
    assert sent["queued_count"] == 3  # the late client is now part of the re-confirmed audience
    rows = _outbox(db, campaign["id"])
    assert len(rows) == 3
    assert len({row.event_key for row in rows}) == 3
    again = _ok(
        client.post(
            f"/businesses/me/campaigns/{campaign['id']}/send", headers=world["headers"]
        ),
        "send again",
    )
    assert again["queued_count"] == 3
    assert len(_outbox(db, campaign["id"])) == 3

    delivered = _deliver_capture(db)
    assert delivered["captured"] == 3
    stats = _ok(
        client.get(
            f"/businesses/me/campaigns/{campaign['id']}/stats", headers=world["headers"]
        ),
        "stats",
    )
    assert stats["status"] == "sent"
    assert stats["captured"] == 3
    assert stats["sent"] == 0 and stats["delivered"] == 0
    assert stats["opens_tracked"] is False and stats["revenue_tracked"] is False


def test_campaign_text_renders_name_business_and_signed_unsubscribe(client, db, world):
    recipient = _add_client(client, world, "Render Target", "render@example.com")
    _consent(db, world, recipient)
    _age_relation(db, world["business"]["id"], recipient["id"], 200)
    _completed_visit(db, world, recipient["id"], days_ago=100)
    campaign = _create(
        client,
        world,
        subject="Hello {name}",
        body="{name}, come to {business}",
    )
    _ok(
        _confirm(client, world, campaign["id"]),
        "confirm",
    )
    _ok(
        client.post(
            f"/businesses/me/campaigns/{campaign['id']}/send", headers=world["headers"]
        ),
        "send",
    )
    row = _outbox(db, campaign["id"])[0]
    message = notification_templates.render_outbox_notification(db, row)
    assert message.subject == "Hello Render Target"
    assert "Render Target, come to Alpha Salon" in message.text
    match = re.search(r"token=([^\s&]+)", message.text)
    assert match
    token = unquote(match.group(1))

    preview = _ok(
        client.get(f"/public/campaigns/unsubscribe?token={quote(token, safe='')}"),
        "unsubscribe preview",
    )
    assert preview["business_name"] == "Alpha Salon"
    assert preview["email"] == "r***@example.com"
    assert preview["status"] == "subscribed"

    first = _ok(
        client.post("/public/campaigns/unsubscribe", json={"token": token}),
        "unsubscribe",
    )
    assert first["status"] == "unsubscribed"
    second = _ok(
        client.post("/public/campaigns/unsubscribe", json={"token": token}),
        "unsubscribe again",
    )
    assert second["status"] == "unsubscribed"

    consent = db.get(MarketingConsent, row.payload["consent_id"])
    assert consent.status == "unsubscribed"
    assert consent.revoked_source == "unsubscribe_link"
    assert consent.revoked_campaign_id == campaign["id"]
    stats = _ok(
        client.get(
            f"/businesses/me/campaigns/{campaign['id']}/stats", headers=world["headers"]
        ),
        "stats",
    )
    assert stats["unsubscribed"] == 1


def test_substituted_unsubscribe_token_does_not_touch_another_contact(client, db, world):
    first = _add_client(client, world, "First", "first@example.com")
    second = _add_client(client, world, "Second", "second@example.com")
    for row in (first, second):
        _consent(db, world, row)
        _age_relation(db, world["business"]["id"], row["id"], 200)
    consents = (
        db.query(MarketingConsent)
        .filter(MarketingConsent.business_id == world["business"]["id"])
        .order_by(MarketingConsent.id)
        .all()
    )
    token = campaign_service.unsubscribe_token(consents[0].id, 1)
    parts = token.split(".")
    forged = f"{consents[1].id}.{parts[1]}.{parts[2]}"
    response = client.post("/public/campaigns/unsubscribe", json={"token": forged})
    assert response.status_code == 400
    assert response.json()["error"]["message"] == "This unsubscribe link is invalid"
    db.expire_all()
    assert db.get(MarketingConsent, consents[1].id).status == "subscribed"
    assert db.get(MarketingConsent, consents[0].id).status == "subscribed"


def test_unsubscribed_after_confirmation_is_not_queued(client, db, world):
    keep = _add_client(client, world, "Keep", "keep@example.com")
    leave = _add_client(client, world, "Leave", "leave@example.com")
    for row in (keep, leave):
        _consent(db, world, row)
        _age_relation(db, world["business"]["id"], row["id"], 200)
    campaign = _create(client, world)
    _ok(
        _confirm(client, world, campaign["id"]),
        "confirm",
    )
    leave_consent = (
        db.query(MarketingConsent)
        .filter(MarketingConsent.client_id == leave["id"])
        .one()
    )
    token = campaign_service.unsubscribe_token(leave_consent.id, campaign["id"])
    _ok(client.post("/public/campaigns/unsubscribe", json={"token": token}), "unsubscribe")
    preview = _preview(client, world, campaign["id"])
    assert preview["recipient_count"] == 2
    assert preview["excluded_after_confirmation"] == 1
    assert preview["send_ready"] is True
    sent = _ok(
        client.post(
            f"/businesses/me/campaigns/{campaign['id']}/send", headers=world["headers"]
        ),
        "send",
    )
    assert sent["queued_count"] == 1
    rows = _outbox(db, campaign["id"])
    assert [row.recipient for row in rows] == ["keep@example.com"]
    stats = _ok(
        client.get(
            f"/businesses/me/campaigns/{campaign['id']}/stats", headers=world["headers"]
        ),
        "stats",
    )
    assert stats["excluded_after_confirmation"] == 1


def test_cancel_stops_pending_messages_before_the_worker(client, db, world):
    recipient = _add_client(client, world, "Cancel Target", "cancel@example.com")
    _consent(db, world, recipient)
    _age_relation(db, world["business"]["id"], recipient["id"], 200)
    campaign = _create(client, world)
    _ok(
        _confirm(client, world, campaign["id"]),
        "confirm",
    )
    _ok(
        client.post(
            f"/businesses/me/campaigns/{campaign['id']}/send", headers=world["headers"]
        ),
        "send",
    )
    cancelled = _ok(
        client.post(
            f"/businesses/me/campaigns/{campaign['id']}/cancel", headers=world["headers"]
        ),
        "cancel",
    )
    assert cancelled["status"] == "cancelled"
    rows = _outbox(db, campaign["id"])
    assert [row.status for row in rows] == ["cancelled"]
    result = _deliver_capture(db)
    assert result["claimed"] == 0
    assert _outbox(db, campaign["id"])[0].status == "cancelled"


def test_send_requires_enabled_notifications_and_a_ready_provider(
    client, db, world, monkeypatch
):
    from app.api.core.config import settings

    recipient = _add_client(client, world, "Gate Target", "gate@example.com")
    _consent(db, world, recipient)
    _age_relation(db, world["business"]["id"], recipient["id"], 200)
    campaign = _create(client, world)
    _ok(
        _confirm(client, world, campaign["id"]),
        "confirm",
    )
    _ok(
        client.put(
            "/businesses/me/notification-settings",
            json={"is_enabled": False},
            headers=world["headers"],
        ),
        "disable notifications",
    )
    disabled = client.post(
        f"/businesses/me/campaigns/{campaign['id']}/send", headers=world["headers"]
    )
    assert disabled.status_code == 409
    assert disabled.json()["error"]["message"] == (
        "Client notifications are turned off for this business"
    )

    _enable_notifications(client, world)
    monkeypatch.setattr(settings, "smtp_host", "")
    provider_down = client.post(
        f"/businesses/me/campaigns/{campaign['id']}/send", headers=world["headers"]
    )
    assert provider_down.status_code == 409
    assert provider_down.json()["error"]["message"] == (
        "The email provider is not connected yet"
    )
    assert _outbox(db, campaign["id"]) == []


def test_recipient_limit_is_checked_at_confirmation(client, db, world, monkeypatch):
    monkeypatch.setattr(campaign_service, "MAX_RECIPIENTS", 1)
    for index in range(2):
        row = _add_client(client, world, f"Limit {index}", f"limit{index}@example.com")
        _consent(db, world, row)
        _age_relation(db, world["business"]["id"], row["id"], 200)
    campaign = _create(client, world)
    preview = _preview(client, world, campaign["id"])
    assert preview["over_limit"] is True
    response = _confirm(client, world, campaign["id"], preview)
    assert response.status_code == 409
    assert "Too many recipients" in response.json()["error"]["message"]


def test_another_business_cannot_read_or_send_the_campaign(client, db, world, make_user, headers_for):
    recipient = _add_client(client, world, "Owner Client", "owner.client@example.com")
    _consent(db, world, recipient)
    _age_relation(db, world["business"]["id"], recipient["id"], 200)
    campaign = _create(client, world)
    intruder = make_user(
        "business_owner", full_name="Intruder", email="intruder.owner@example.com"
    )
    intruder_headers = headers_for(intruder)
    _ok(
        client.post(
            "/businesses/", json={"name": "Beta Salon", "business_type": "salon"}, headers=intruder_headers
        ),
        "intruder business",
    )
    for method, path in (
        ("get", f"/businesses/me/campaigns/{campaign['id']}"),
        ("get", f"/businesses/me/campaigns/{campaign['id']}/preview"),
        ("post", f"/businesses/me/campaigns/{campaign['id']}/confirm"),
        ("post", f"/businesses/me/campaigns/{campaign['id']}/send"),
        ("post", f"/businesses/me/campaigns/{campaign['id']}/cancel"),
    ):
        body = {"json": {"audience_hash": "x", "content_hash": "y"}} if path.endswith("/confirm") else {}
        response = getattr(client, method)(path, headers=intruder_headers, **body)
        assert response.status_code == 404, f"{method} {path}: {response.status_code}"
    assert client.get(
        f"/businesses/me/clients/{recipient['id']}/marketing-consent",
        headers=intruder_headers,
    ).status_code == 404


def test_staff_without_owner_rights_cannot_touch_campaigns(client, db, world, headers_for):
    from app.db.models.user import User

    employee_user = db.get(User, world["employee_user_id"])
    employee_headers = headers_for(employee_user)
    assert client.get("/businesses/me/campaigns", headers=employee_headers).status_code == 403
    assert (
        client.post(
            "/businesses/me/campaigns",
            json=_campaign_body(),
            headers=employee_headers,
        ).status_code
        == 403
    )


def test_owner_can_read_and_revoke_consent_idempotently(client, db, world):
    person = _add_client(client, world, "Consent Person", "consent.person@example.com")
    missing = client.get(
        f"/businesses/me/clients/{person['id']}/marketing-consent",
        headers=world["headers"],
    )
    assert missing.status_code == 404
    consent = _consent(db, world, person, source="public_booking")
    read = _ok(
        client.get(
            f"/businesses/me/clients/{person['id']}/marketing-consent",
            headers=world["headers"],
        ),
        "read consent",
    )
    assert read["status"] == "subscribed"
    assert read["source"] == "public_booking"
    first = _ok(
        client.post(
            f"/businesses/me/clients/{person['id']}/marketing-consent/revoke",
            headers=world["headers"],
        ),
        "revoke",
    )
    second = _ok(
        client.post(
            f"/businesses/me/clients/{person['id']}/marketing-consent/revoke",
            headers=world["headers"],
        ),
        "revoke again",
    )
    assert first["status"] == second["status"] == "unsubscribed"
    assert first["revoked_at"] == second["revoked_at"]
    assert second["revoked_source"] == "owner_request"
    db.refresh(consent)
    assert consent.status == "unsubscribed"


def test_confirm_refuses_text_or_audience_the_owner_did_not_preview(client, db, world):
    first = _add_client(client, world, "Seen A", "seen.a@example.com")
    _consent(db, world, first)
    _age_relation(db, world["business"]["id"], first["id"], 200)
    _completed_visit(db, world, first["id"], days_ago=100)
    campaign = _create(client, world)
    seen = _preview(client, world, campaign["id"])

    _ok(client.patch(f"/businesses/me/campaigns/{campaign['id']}", json={"body": "Different text nobody saw"},
                     headers=world["headers"]), "edit after preview")
    stale_text = _confirm(client, world, campaign["id"], seen)
    assert stale_text.status_code == 409, stale_text.text

    seen = _preview(client, world, campaign["id"])
    late = _add_client(client, world, "Late B", "late.b@example.com")
    _consent(db, world, late)
    _age_relation(db, world["business"]["id"], late["id"], 200)
    _completed_visit(db, world, late["id"], days_ago=100)
    stale_audience = _confirm(client, world, campaign["id"], seen)
    assert stale_audience.status_code == 409, stale_audience.text

    db.expire_all()
    assert _ok(client.get(f"/businesses/me/campaigns/{campaign['id']}", headers=world["headers"]), "get")["status"] == "draft"
    assert db.query(NotificationOutbox).filter(NotificationOutbox.campaign_id == campaign["id"]).count() == 0
    assert _confirm(client, world, campaign["id"]).status_code == 200
