"""Guest deposits: server totals, holds, signed webhooks, expiry and refunds."""
import hashlib
import hmac
import json
from datetime import datetime, time, timedelta, timezone
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pytest

from app.api.core.clock import utcnow
from app.db.models.business_audit_event import BusinessAuditEvent
from app.db.models.service_payment import (
    ServicePaymentAttempt,
    ServicePaymentEvent,
    ServicePaymentRefund,
)
from app.db.models.stripe_checkout import StripeCheckoutState
from app.db.models.user import User
from app.db.models.visit import Visit
from app.db.models.visit_item import VisitItem

ZONE = ZoneInfo("Europe/Prague")
SECRET = "local-sandbox-webhook-secret-123456"
MERCHANT = "xtrix-service-sandbox"


def _ok(response, what):
    assert response.status_code in (200, 201, 202), f"{what}: {response.status_code} {response.text}"
    return response.json()


@pytest.fixture
def deposit_salon(client, make_user, headers_for, monkeypatch):
    from app.api.core.config import settings

    monkeypatch.setattr(settings, "service_payment_provider", "sandbox")
    monkeypatch.setattr(settings, "service_payment_merchant", MERCHANT)
    monkeypatch.setattr(settings, "service_payment_webhook_secret", SECRET)
    monkeypatch.setattr(settings, "frontend_url", "http://localhost:3170")

    owner = make_user("business_owner", full_name="Deposit Owner", email="deposit.owner@example.com")
    headers = headers_for(owner)
    business = _ok(
        client.post(
            "/businesses/",
            json={"name": "Deposit Salon", "business_type": "salon", "timezone": "Europe/Prague"},
            headers=headers,
        ),
        "business",
    )
    public = _ok(
        client.put(
            "/businesses/me/public-booking",
            json={
                "is_enabled": True,
                "deposit_type": "fixed",
                "deposit_value": 12.30,
                "deposit_hold_minutes": 30,
            },
            headers=headers,
        ),
        "public settings",
    )
    service = _ok(
        client.post(
            "/services/",
            json={
                "name": "Deposit Cut",
                "price": 20.10,
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
    start = (
        datetime.combine(local_day, time(10, 0), tzinfo=ZONE)
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
    )
    return {
        "owner": owner,
        "headers": headers,
        "business": business,
        "slug": public["slug"],
        "service": service,
        "start": start,
        "day": local_day,
    }


def _book(client, salon, start=None, request_id="deposit-request-1", name="Deposit Guest"):
    start = start or salon["start"]
    return client.post(
        f"/public/booking/{salon['slug']}/bookings",
        json={
            "service_id": salon["service"]["id"],
            "staff_id": salon["owner"].id,
            "start": start.isoformat() + "Z",
            "full_name": name,
            "email": f"{request_id}@example.com",
            "processing_consent": True,
            "client_request_id": request_id,
        },
    )


def _payment_token(booking) -> str:
    fragment = urlparse(booking["payment"]["checkout_url"]).fragment
    return parse_qs(fragment)["token"][0]


def _webhook(client, attempt, event_type, event_id, amount=None, currency=None, secret=SECRET, merchant=MERCHANT):
    payload = {
        "id": event_id,
        "type": event_type,
        "provider": "sandbox",
        "merchant": MERCHANT,
        "payment_ref": attempt.provider_ref,
        "amount": amount if amount is not None else str(attempt.amount),
        "currency": currency or attempt.currency,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post(
        "/public/booking/payments/webhooks/sandbox",
        content=raw,
        headers={
            "X-Service-Payment-Signature": signature,
            "X-Service-Payment-Merchant": merchant,
            "Content-Type": "application/json",
        },
    )


def _attempt(db, booking):
    attempt = (
        db.query(ServicePaymentAttempt)
        .filter(ServicePaymentAttempt.public_ref == booking["payment"]["public_ref"])
        .first()
    )
    assert attempt is not None
    return attempt


def _audit(db, business_id, action):
    db.expire_all()
    return (
        db.query(BusinessAuditEvent)
        .filter(
            BusinessAuditEvent.business_id == business_id,
            BusinessAuditEvent.action == action,
        )
        .order_by(BusinessAuditEvent.id)
        .all()
    )


def test_fixed_deposit_holds_slot_and_uses_server_total(client, db, deposit_salon):
    booking = _ok(_book(client, deposit_salon), "deposit booking")
    assert booking["status"] == "awaiting_payment"
    assert booking["price"] == pytest.approx(20.10)
    payment = booking["payment"]
    assert payment["status"] == "pending"
    assert payment["amount"] == pytest.approx(12.30)
    assert payment["remaining_amount"] == pytest.approx(7.80)
    assert payment["checkout_url"].startswith("http://localhost:3170/payment/sandbox#")
    assert "/payment/sandbox#" in payment["checkout_url"]

    assert db.query(ServicePaymentAttempt).count() == 1
    assert db.query(StripeCheckoutState).count() == 0

    settings_out = _ok(
        client.get("/businesses/me/public-booking", headers=deposit_salon["headers"]),
        "settings",
    )
    assert settings_out["deposit_type"] == "fixed"
    assert settings_out["payments_available"] is True
    card = _ok(client.get(f"/public/booking/{deposit_salon['slug']}"), "public card")
    assert card["deposit_type"] == "fixed"
    assert card["deposit_value"] == pytest.approx(12.30)

    day = deposit_salon["day"].isoformat()
    slots = _ok(
        client.get(
            f"/public/booking/{deposit_salon['slug']}/slots",
            params={
                "service_id": deposit_salon["service"]["id"],
                "staff_id": deposit_salon["owner"].id,
                "date_from": day,
                "date_to": day,
            },
        ),
        "slots while held",
    )
    starts = [slot["start"] for slot in slots["days"][0]["slots"]]
    assert deposit_salon["start"].isoformat() + "Z" not in starts

    replay = _ok(_book(client, deposit_salon), "replay")
    assert replay["id"] == booking["id"]
    assert replay["payment"]["public_ref"] == payment["public_ref"]
    assert db.query(ServicePaymentAttempt).count() == 1


def test_percentage_deposit_is_rounded_and_capped(client, deposit_salon):
    _ok(
        client.put(
            "/businesses/me/public-booking",
            json={"deposit_type": "percentage", "deposit_value": 33.33},
            headers=deposit_salon["headers"],
        ),
        "percentage policy",
    )
    booking = _ok(_book(client, deposit_salon), "percentage booking")
    assert booking["payment"]["amount"] == pytest.approx(6.70)
    assert booking["payment"]["remaining_amount"] == pytest.approx(13.40)

    _ok(
        client.put(
            "/businesses/me/public-booking",
            json={"deposit_type": "fixed", "deposit_value": 999},
            headers=deposit_salon["headers"],
        ),
        "capped policy",
    )
    other_start = deposit_salon["start"] + timedelta(hours=2)
    capped = _ok(_book(client, deposit_salon, other_start, "deposit-request-2"), "capped booking")
    assert capped["payment"]["amount"] == pytest.approx(20.10)
    assert capped["payment"]["remaining_amount"] == pytest.approx(0)


def test_bad_deposit_policy_is_refused(client, deposit_salon):
    zero = client.put(
        "/businesses/me/public-booking",
        json={"deposit_type": "fixed", "deposit_value": 0},
        headers=deposit_salon["headers"],
    )
    assert zero.status_code == 422, zero.text
    too_much = client.put(
        "/businesses/me/public-booking",
        json={"deposit_type": "percentage", "deposit_value": 101},
        headers=deposit_salon["headers"],
    )
    assert too_much.status_code == 422, too_much.text


def test_paid_webhook_confirms_once_and_duplicate_does_nothing(client, db, deposit_salon):
    booking = _ok(_book(client, deposit_salon), "booking")
    attempt = _attempt(db, booking)

    first = _ok(_webhook(client, attempt, "payment.paid", "evt-paid-1"), "paid webhook")
    assert first["duplicate"] is False
    assert first["status"] == "paid"

    db.expire_all()
    visit = db.get(Visit, booking["id"])
    assert visit.status == "planned"
    assert _attempt(db, booking).paid_at is not None
    assert db.query(ServicePaymentEvent).count() == 1

    paid_events = _audit(db, deposit_salon["business"]["id"], "payment.paid")
    assert len(paid_events) == 1
    assert paid_events[0].entity_id == attempt.id
    assert paid_events[0].actor_role == "system"
    assert paid_events[0].request_id == "evt-paid-1"
    assert paid_events[0].changes == {
        "amount": format(attempt.amount, "f"),
        "currency": attempt.currency,
        "status": "paid",
        "provider": "sandbox",
        "provider_event_id": "evt-paid-1",
    }

    duplicate = _ok(_webhook(client, attempt, "payment.paid", "evt-paid-1"), "duplicate webhook")
    assert duplicate["duplicate"] is True
    db.expire_all()
    assert db.get(Visit, booking["id"]).status == "planned"
    assert db.query(ServicePaymentEvent).count() == 1
    assert len(_audit(db, deposit_salon["business"]["id"], "payment.paid")) == 1

    stale_failure = _ok(
        _webhook(client, attempt, "payment.failed", "evt-failed-after-paid"), "late failure"
    )
    assert stale_failure["status"] == "paid"
    db.expire_all()
    assert _attempt(db, booking).status == "paid"
    assert db.get(Visit, booking["id"]).status == "planned"
    assert _audit(db, deposit_salon["business"]["id"], "payment.failed") == []

    status = _ok(
        client.get(
            f"/public/booking/payments/{booking['payment']['public_ref']}",
            headers={"X-Payment-Token": _payment_token(booking)},
        ),
        "payment status",
    )
    assert status["status"] == "paid"
    assert status["visit_status"] == "planned"
    assert status["checkout_url"] is None


def test_webhook_signature_merchant_and_amount_are_checked(client, db, deposit_salon):
    booking = _ok(_book(client, deposit_salon), "booking")
    attempt = _attempt(db, booking)

    unsigned = client.post(
        "/public/booking/payments/webhooks/sandbox",
        content=b"{}",
        headers={"Content-Type": "application/json"},
    )
    assert unsigned.status_code == 400, unsigned.text

    wrong_signature = _webhook(
        client, attempt, "payment.paid", "evt-bad-sig", secret="different-secret-value-123"
    )
    assert wrong_signature.status_code == 400, wrong_signature.text

    wrong_merchant = _webhook(
        client, attempt, "payment.paid", "evt-bad-merchant", merchant="someone-else"
    )
    assert wrong_merchant.status_code == 400, wrong_merchant.text

    wrong_amount = _webhook(client, attempt, "payment.paid", "evt-bad-amount", amount="1.00")
    assert wrong_amount.status_code == 400, wrong_amount.text

    db.expire_all()
    assert _attempt(db, booking).status == "pending"
    assert db.get(Visit, booking["id"]).status == "awaiting_payment"
    assert db.query(ServicePaymentEvent).count() == 0


def test_failed_payment_releases_hold_and_late_paid_goes_to_reconcile(client, db, deposit_salon):
    booking = _ok(_book(client, deposit_salon), "booking")
    attempt = _attempt(db, booking)

    failed = _ok(_webhook(client, attempt, "payment.failed", "evt-failed-1"), "failed webhook")
    assert failed["status"] == "failed"
    db.expire_all()
    assert db.get(Visit, booking["id"]).status == "cancelled"
    failed_events = _audit(db, deposit_salon["business"]["id"], "payment.failed")
    assert len(failed_events) == 1
    assert failed_events[0].entity_id == attempt.id
    assert failed_events[0].request_id == "evt-failed-1"
    assert failed_events[0].changes["status"] == "failed"

    other = _ok(_book(client, deposit_salon, request_id="deposit-request-2", name="Second Guest"), "rebook")
    assert other["id"] != booking["id"]

    late = _ok(_webhook(client, attempt, "payment.paid", "evt-paid-late"), "late paid")
    assert late["status"] == "reconcile"
    db.expire_all()
    assert db.get(Visit, booking["id"]).status == "cancelled"
    assert db.get(Visit, other["id"]).status == "awaiting_payment"
    assert _attempt(db, booking).refunded_amount == 0
    late_events = _audit(db, deposit_salon["business"]["id"], "payment.paid")
    assert len(late_events) == 1
    assert late_events[0].request_id == "evt-paid-late"
    assert late_events[0].changes["status"] == "reconcile"


def test_expired_hold_frees_the_slot_and_late_payment_is_reconcile(client, db, deposit_salon):
    booking = _ok(_book(client, deposit_salon), "booking")
    attempt = _attempt(db, booking)
    attempt.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()

    day = deposit_salon["day"].isoformat()
    slots = _ok(
        client.get(
            f"/public/booking/{deposit_salon['slug']}/slots",
            params={
                "service_id": deposit_salon["service"]["id"],
                "staff_id": deposit_salon["owner"].id,
                "date_from": day,
                "date_to": day,
            },
        ),
        "slots after expiry",
    )
    assert deposit_salon["start"].isoformat() + "Z" in [
        slot["start"] for slot in slots["days"][0]["slots"]
    ]
    db.expire_all()
    assert _attempt(db, booking).status == "expired"
    assert db.get(Visit, booking["id"]).status == "cancelled"

    late = _ok(_webhook(client, attempt, "payment.paid", "evt-paid-after-expiry"), "late paid")
    assert late["status"] == "reconcile"


def test_conflicting_visit_at_payment_time_is_not_confirmed(client, db, deposit_salon):
    booking = _ok(_book(client, deposit_salon), "booking")
    attempt = _attempt(db, booking)
    held = db.get(Visit, booking["id"])

    conflicting = Visit(
        client_id=held.client_id,
        specialist_id=held.specialist_id,
        business_id=held.business_id,
        service_id=deposit_salon["service"]["id"],
        service_name="Walk-in",
        date_start=held.date_start - timedelta(minutes=25),
        date_end=held.date_start,
        price=20.10,
        status="planned",
    )
    db.add(conflicting)
    db.flush()
    db.add(
        VisitItem(
            visit_id=conflicting.id,
            position=0,
            service_id=deposit_salon["service"]["id"],
            specialist_id=held.specialist_id,
            service_name="Walk-in",
            price=20.10,
            duration_minutes=25,
            buffer_before_minutes=0,
            buffer_after_minutes=10,
            date_start=conflicting.date_start,
            date_end=conflicting.date_end,
        )
    )
    db.commit()

    result = _ok(_webhook(client, attempt, "payment.paid", "evt-paid-conflict"), "conflict paid")
    assert result["status"] == "reconcile"
    db.expire_all()
    assert db.get(Visit, booking["id"]).status == "cancelled"
    assert db.get(Visit, conflicting.id).status == "planned"


def test_payment_requires_its_guest_token(client, db, deposit_salon):
    booking = _ok(_book(client, deposit_salon), "booking")
    ref = booking["payment"]["public_ref"]
    assert client.get(f"/public/booking/payments/{ref}").status_code == 404
    assert client.get(
        f"/public/booking/payments/{ref}", headers={"X-Payment-Token": "wrong-token"}
    ).status_code == 404
    status = _ok(
        client.get(f"/public/booking/payments/{ref}", headers={"X-Payment-Token": _payment_token(booking)}),
        "status",
    )
    assert status["status"] == "pending"


def test_payment_token_header_is_allowed_by_cors(client):
    response = client.options(
        "/public/booking/payments/some-ref",
        headers={
            "Origin": "http://localhost:3170",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-Payment-Token",
        },
    )
    assert response.status_code == 200, response.text
    assert "x-payment-token" in response.headers["access-control-allow-headers"].lower()


def test_refunds_are_idempotent_and_capped(client, db, deposit_salon):
    booking = _ok(_book(client, deposit_salon), "booking")
    attempt = _attempt(db, booking)
    _ok(_webhook(client, attempt, "payment.paid", "evt-refund-paid"), "paid")

    employee = _ok(
        client.post(
            "/employees/create-with-user",
            json={"full_name": "No Money", "email": "deposit.staff@example.com"},
            headers=deposit_salon["headers"],
        ),
        "employee",
    )
    from app.api.core.security import create_access_token

    staff_headers = {"Authorization": f"Bearer {create_access_token({'sub': str(employee['employee_user_id'])})}"}
    assert client.get("/businesses/me/service-payments", headers=staff_headers).status_code == 403
    assert client.post(
        f"/businesses/me/service-payments/{attempt.id}/refunds",
        json={"refund_key": "refund-staff-1"},
        headers=staff_headers,
    ).status_code == 403

    first = _ok(
        client.post(
            f"/businesses/me/service-payments/{attempt.id}/refunds",
            json={"refund_key": "refund-part-1", "amount": 5, "reason": "secret-refund-reason"},
            headers={**deposit_salon["headers"], "X-Request-ID": "refund-request-1"},
        ),
        "partial refund",
    )
    assert first["amount"] == pytest.approx(5)
    replay = _ok(
        client.post(
            f"/businesses/me/service-payments/{attempt.id}/refunds",
            json={"refund_key": "refund-part-1", "amount": 5},
            headers={**deposit_salon["headers"], "X-Request-ID": "refund-request-1"},
        ),
        "refund replay",
    )
    assert replay["id"] == first["id"]
    db.expire_all()
    assert db.query(ServicePaymentRefund).count() == 1
    assert _attempt(db, booking).status == "partially_refunded"
    assert float(_attempt(db, booking).refunded_amount) == pytest.approx(5)
    refund_events = _audit(db, deposit_salon["business"]["id"], "payment.refunded")
    assert len(refund_events) == 1
    assert refund_events[0].entity_id == attempt.id
    assert refund_events[0].actor_name == "Deposit Owner"
    assert refund_events[0].request_id == "refund-request-1"
    assert refund_events[0].changes["amount"] == "5.00"
    assert refund_events[0].changes["refund_id"] == first["id"]
    assert "reason" not in refund_events[0].changes
    assert "secret-refund-reason" not in json.dumps(refund_events[0].changes)

    too_big = client.post(
        f"/businesses/me/service-payments/{attempt.id}/refunds",
        json={"refund_key": "refund-part-2", "amount": 8},
        headers=deposit_salon["headers"],
    )
    assert too_big.status_code == 422, too_big.text

    rest = _ok(
        client.post(
            f"/businesses/me/service-payments/{attempt.id}/refunds",
            json={"refund_key": "refund-rest-1"},
            headers={**deposit_salon["headers"], "X-Request-ID": "refund-request-2"},
        ),
        "full refund",
    )
    assert rest["amount"] == pytest.approx(7.30)
    db.expire_all()
    assert _attempt(db, booking).status == "refunded"
    assert float(_attempt(db, booking).refunded_amount) == pytest.approx(12.30)
    refund_events = _audit(db, deposit_salon["business"]["id"], "payment.refunded")
    assert len(refund_events) == 2
    assert [event.changes["amount"] for event in refund_events] == ["5.00", "7.30"]
    assert refund_events[1].request_id == "refund-request-2"

    again = client.post(
        f"/businesses/me/service-payments/{attempt.id}/refunds",
        json={"refund_key": "refund-rest-2"},
        headers=deposit_salon["headers"],
    )
    assert again.status_code == 409, again.text

    listing = _ok(
        client.get("/businesses/me/service-payments", headers=deposit_salon["headers"]),
        "payment list",
    )
    assert listing[0]["status"] == "refunded"
    assert listing[0]["refunded_amount"] == pytest.approx(12.30)
    assert listing[0]["refundable_amount"] == 0


def test_provider_disabled_keeps_pay_on_site(client, db, deposit_salon, monkeypatch):
    from app.api.core.config import settings

    monkeypatch.setattr(settings, "service_payment_provider", "")
    booking = _ok(_book(client, deposit_salon), "booking")
    assert booking["status"] == "planned"
    assert booking["payment"] is None
    assert db.query(ServicePaymentAttempt).count() == 0

    public = _ok(client.get(f"/public/booking/{deposit_salon['slug']}"), "public card")
    assert public["deposit_type"] == "none"
    settings_out = _ok(
        client.get("/businesses/me/public-booking", headers=deposit_salon["headers"]),
        "settings",
    )
    assert settings_out["payments_available"] is False


def test_provider_failure_does_not_confirm_or_hold_slot(client, db, deposit_salon, monkeypatch):
    from app.api.core.service_payment_provider import SandboxProvider

    def boom(self, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(SandboxProvider, "create_checkout", boom)
    response = _book(client, deposit_salon)
    assert response.status_code == 502, response.text
    assert db.query(Visit).count() == 0
    assert db.query(ServicePaymentAttempt).count() == 0

    day = deposit_salon["day"].isoformat()
    slots = _ok(
        client.get(
            f"/public/booking/{deposit_salon['slug']}/slots",
            params={
                "service_id": deposit_salon["service"]["id"],
                "staff_id": deposit_salon["owner"].id,
                "date_from": day,
                "date_to": day,
            },
        ),
        "slots after provider failure",
    )
    assert deposit_salon["start"].isoformat() + "Z" in [
        slot["start"] for slot in slots["days"][0]["slots"]
    ]


def test_commit_false_caller_keeps_a_hold_for_its_own_transaction(client, db, deposit_salon):
    from app.api.core import public_booking_service
    from app.db.models.business import Business
    from app.schemas.public_booking import PublicBookingCreate

    business = db.get(Business, deposit_salon["business"]["id"])
    payload = PublicBookingCreate(
        service_id=deposit_salon["service"]["id"],
        staff_id=deposit_salon["owner"].id,
        start=deposit_salon["start"],
        full_name="Waitlist Guest",
        email="waitlist.deposit@example.com",
        processing_consent=True,
        client_request_id="waitlist-style-1",
    )
    visit = public_booking_service.submit_booking(db, business, payload, "en", commit=False)
    assert visit.status == "awaiting_payment"
    attempt = (
        db.query(ServicePaymentAttempt)
        .filter(ServicePaymentAttempt.visit_id == visit.id)
        .one()
    )
    assert attempt.status == "pending"
    db.commit()


def test_guest_cancellation_releases_an_unpaid_hold(client, db, deposit_salon):
    booking = _ok(_book(client, deposit_salon), "booking")
    cancel = client.post(
        "/public/booking-management/cancel",
        headers={"X-Booking-Token": booking["manage_token"]},
    )
    assert cancel.status_code == 200, cancel.text
    assert cancel.json()["status"] == "cancelled"
    db.expire_all()
    assert _attempt(db, booking).status == "cancelled"
    assert db.get(Visit, booking["id"]).status == "cancelled"

    day = deposit_salon["day"].isoformat()
    slots = _ok(
        client.get(
            f"/public/booking/{deposit_salon['slug']}/slots",
            params={
                "service_id": deposit_salon["service"]["id"],
                "staff_id": deposit_salon["owner"].id,
                "date_from": day,
                "date_to": day,
            },
        ),
        "slots after guest cancel",
    )
    assert deposit_salon["start"].isoformat() + "Z" in [
        slot["start"] for slot in slots["days"][0]["slots"]
    ]
