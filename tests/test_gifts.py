"""Gift certificates and visit packages: ledger, idempotency, scope and tenancy."""
import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from app.api.core.clock import utcnow
from app.db.models.gift import (
    GiftCertificate,
    GiftLedgerEntry,
    GiftOffer,
    GiftOrder,
    GiftPaymentEvent,
)
from tests.tenant_world import build_tenant

SECRET = "local-sandbox-webhook-secret-123456"
MERCHANT = "xtrix-service-sandbox"
BUYER = {
    "buyer_name": "Gift Buyer",
    "buyer_email": "gift.buyer@example.com",
}


def _ok(response, what):
    assert response.status_code in (200, 201), f"{what}: {response.status_code} {response.text}"
    return response.json()


def _offer_body(kind="money", service_ids=None, **overrides):
    body = {
        "name": "Gift Money" if kind == "money" else "Gift Package",
        "kind": kind,
        "price": 40 if kind == "money" else 250,
        "face_value": 50 if kind == "money" else None,
        "visits_count": 3 if kind == "package" else None,
        "validity_days": 60,
        "service_ids": service_ids or [],
        "is_active": True,
    }
    body.update(overrides)
    return body


@pytest.fixture
def gift_world(client, db, make_user, headers_for, monkeypatch):
    from app.api.core.config import settings

    monkeypatch.setattr(settings, "service_payment_provider", "sandbox")
    monkeypatch.setattr(settings, "service_payment_merchant", MERCHANT)
    monkeypatch.setattr(settings, "service_payment_webhook_secret", SECRET)
    monkeypatch.setattr(settings, "frontend_url", "http://localhost:3170")

    tenant = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    public = _ok(
        client.get("/businesses/me/public-booking", headers=tenant["headers"]),
        "public settings",
    )
    money = _ok(
        client.post(
            "/businesses/me/gift-offers",
            json=_offer_body("money", [tenant["service"]["id"]]),
            headers=tenant["headers"],
        ),
        "money offer",
    )
    package = _ok(
        client.post(
            "/businesses/me/gift-offers",
            json=_offer_body("package", [tenant["service"]["id"]]),
            headers=tenant["headers"],
        ),
        "package offer",
    )
    tenant.update(
        {"slug": public["slug"], "money_offer": money, "package_offer": package}
    )
    return tenant


def _shop(client, world):
    return _ok(client.get(f"/public/gifts/{world['slug']}"), "gift shop")


def _purchase(client, world, offer_id=None, request_key="gift-request-1", **overrides):
    body = {
        "offer_id": offer_id or world["money_offer"]["id"],
        "client_request_id": request_key,
        **BUYER,
        "recipient_name": "Gift Recipient",
        "recipient_email": "gift.recipient@example.com",
        "message": "Happy birthday",
        "processing_consent": True,
    }
    body.update(overrides)
    return client.post(
        f"/public/gifts/{world['slug']}/orders",
        json=body,
    )


def _token(order_json):
    fragment = urlparse(order_json["checkout_url"]).fragment
    return parse_qs(fragment)["token"][0]


def _sandbox(client, order_json, outcome="paid"):
    return client.post(
        f"/public/gifts/orders/{order_json['public_ref']}/sandbox",
        json={"outcome": outcome},
        headers={"X-Gift-Token": _token(order_json)},
    )


def _pay(client, world, offer_id=None, request_key="gift-request-1", **overrides):
    order_json = _ok(_purchase(client, world, offer_id, request_key, **overrides), "purchase")
    paid = _ok(_sandbox(client, order_json), "sandbox payment")
    return order_json, paid


def _order(db, paid):
    return (
        db.query(GiftOrder)
        .filter(GiftOrder.public_ref == paid["public_ref"])
        .one()
    )


def _certificate(db, paid):
    return (
        db.query(GiftCertificate)
        .filter(GiftCertificate.order_id == _order(db, paid).id)
        .one()
    )


def _webhook(
    client,
    order,
    event_type,
    event_id,
    amount=None,
    currency=None,
    secret=SECRET,
    merchant=MERCHANT,
):
    payload = {
        "id": event_id,
        "type": event_type,
        "provider": "sandbox",
        "merchant": MERCHANT,
        "payment_ref": order.provider_ref,
        "amount": amount if amount is not None else str(order.amount),
        "currency": currency or order.currency,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post(
        "/public/gifts/webhooks/sandbox",
        content=raw,
        headers={
            "X-Service-Payment-Signature": signature,
            "X-Service-Payment-Merchant": merchant,
            "Content-Type": "application/json",
        },
    )


def _new_visit(client, world, service_id=None, price=500):
    start = (utcnow() + timedelta(days=6)).replace(microsecond=0)
    return _ok(
        client.post(
            "/visits/",
            json={
                "client_id": world["client"]["id"],
                "specialist_id": world["owner"].id,
                "service_id": service_id or world["service"]["id"],
                "date_start": start.isoformat(),
                "date_end": (start + timedelta(hours=1)).isoformat(),
                "price": price,
            },
            headers=world["headers"],
        ),
        "visit",
    )


def test_shop_lists_published_offers_and_checks_service_ownership(client, gift_world, make_user, headers_for):
    shop = _shop(client, gift_world)
    assert shop["business_name"] == "Alpha Salon"
    assert shop["currency"] == "EUR"
    assert shop["payments_available"] is True
    assert {row["kind"] for row in shop["offers"]} == {"money", "package"}

    _ok(
        client.put(
            f"/businesses/me/gift-offers/{gift_world['package_offer']['id']}",
            json=_offer_body("package", [gift_world["service"]["id"]], is_active=False),
            headers=gift_world["headers"],
        ),
        "deactivate",
    )
    shop = _shop(client, gift_world)
    assert [row["kind"] for row in shop["offers"]] == ["money"]

    bravo = build_tenant(client, make_user, headers_for, "Bravo", "+42070022")
    _ok(
        client.get("/businesses/me/public-booking", headers=bravo["headers"]),
        "bravo public settings",
    )
    foreign = client.post(
        "/businesses/me/gift-offers",
        json=_offer_body("money", [gift_world["service"]["id"]]),
        headers=bravo["headers"],
    )
    assert foreign.status_code == 422, foreign.text
    foreign_shop = _ok(client.get("/public/gifts/bravo-salon"), "bravo shop")
    assert foreign_shop["offers"] == []
    assert client.get("/public/gifts/no-such-salon").status_code == 404


def test_offer_units_and_minimums_are_validated(
    client, gift_world, make_user, headers_for
):
    mixed = _offer_body(
        "money",
        [gift_world["service"]["id"]],
        visits_count=2,
    )
    assert client.post(
        "/businesses/me/gift-offers", json=mixed, headers=gift_world["headers"]
    ).status_code == 422
    missing_value = _offer_body("money", [], face_value=None)
    assert client.post(
        "/businesses/me/gift-offers", json=missing_value, headers=gift_world["headers"]
    ).status_code == 422
    missing_visits = _offer_body("package", [], visits_count=None)
    assert client.post(
        "/businesses/me/gift-offers", json=missing_visits, headers=gift_world["headers"]
    ).status_code == 422

    offers = _ok(
        client.get("/businesses/me/gift-offers", headers=gift_world["headers"]),
        "owners offers",
    )
    assert len(offers) == 2
    outsider = make_user("specialist", full_name="Outsider", email="outsider@example.com")
    assert client.get(
        "/businesses/me/gift-offers", headers=headers_for(outsider)
    ).status_code == 403


def test_purchase_is_retry_safe_and_needs_consent(client, db, gift_world):
    refused = _purchase(client, gift_world, request_key="gift-no-consent", processing_consent=False)
    assert refused.status_code == 422, refused.text

    first = _ok(_purchase(client, gift_world), "first purchase")
    assert first["status"] == "pending"
    assert first["checkout_url"].startswith("http://localhost:3170/gift/checkout#")
    assert db.query(GiftOrder).count() == 1

    replay = _ok(_purchase(client, gift_world), "replayed purchase")
    assert replay["public_ref"] == first["public_ref"]
    assert replay["checkout_url"] == first["checkout_url"]
    assert db.query(GiftOrder).count() == 1

    conflict = _purchase(client, gift_world, buyer_name="Another Buyer")
    assert conflict.status_code == 409, conflict.text

    unknown = _purchase(client, gift_world, offer_id=999999, request_key="gift-request-2")
    assert unknown.status_code == 404, unknown.text

    db.expire_all()
    offer = db.get(GiftOffer, gift_world["money_offer"]["id"])
    offer.is_active = False
    db.commit()
    inactive = _purchase(client, gift_world, request_key="gift-request-3")
    assert inactive.status_code == 404, inactive.text


def test_paid_webhook_issues_one_certificate_and_replay_keeps_the_code(client, db, gift_world):
    first = _ok(_purchase(client, gift_world), "purchase")
    db.expire_all()
    order = _order(db, first)
    paid = _ok(
        _webhook(client, order, "payment.paid", "evt-gift-paid-1"), "paid webhook"
    )
    db.expire_all()
    order = _order(db, first)
    certificate = _certificate(db, first)
    assert order.status == "paid"
    assert db.query(GiftCertificate).count() == 1
    issued = (
        db.query(GiftLedgerEntry)
        .filter(GiftLedgerEntry.certificate_id == certificate.id)
        .one()
    )
    assert issued.entry_type == "issued"
    assert float(issued.amount_delta) == pytest.approx(50)

    duplicate = _ok(
        _webhook(client, order, "payment.paid", "evt-gift-paid-1"), "duplicate webhook"
    )
    assert duplicate["duplicate"] is True
    db.expire_all()
    assert db.query(GiftCertificate).count() == 1
    assert db.query(GiftPaymentEvent).count() == 1

    repeat_return = _ok(_sandbox(client, first), "return from checkout")
    assert repeat_return["status"] == "paid"
    assert repeat_return["certificate"]["code"] == certificate.code
    db.expire_all()
    assert db.query(GiftCertificate).count() == 1

    stale_failure = _ok(
        _webhook(client, order, "payment.failed", "evt-gift-failed-after-paid"),
        "late failure",
    )
    assert stale_failure["status"] == "paid"

    assert client.get(
        f"/public/gifts/orders/{first['public_ref']}"
    ).status_code == 404
    assert client.get(
        f"/public/gifts/orders/{first['public_ref']}",
        headers={"X-Gift-Token": "wrong-token"},
    ).status_code == 404
    status = _ok(
        client.get(
            f"/public/gifts/orders/{first['public_ref']}",
            headers={"X-Gift-Token": _token(first)},
        ),
        "buyer order",
    )
    assert status["certificate"]["code"] == certificate.code


def test_gift_webhook_checks_signature_merchant_and_amount(client, db, gift_world):
    order_json = _ok(_purchase(client, gift_world), "purchase")
    db.expire_all()
    order = _order(db, order_json)

    unsigned = client.post(
        "/public/gifts/webhooks/sandbox",
        content=b"{}",
        headers={"Content-Type": "application/json"},
    )
    assert unsigned.status_code == 400, unsigned.text
    wrong_signature = _webhook(
        client, order, "payment.paid", "evt-bad-sig", secret="different-secret-123456"
    )
    assert wrong_signature.status_code == 400, wrong_signature.text
    wrong_merchant = _webhook(
        client, order, "payment.paid", "evt-bad-merchant", merchant="someone-else"
    )
    assert wrong_merchant.status_code == 400, wrong_merchant.text
    wrong_amount = _webhook(
        client, order, "payment.paid", "evt-bad-amount", amount="1.00"
    )
    assert wrong_amount.status_code == 400, wrong_amount.text

    db.expire_all()
    assert _order(db, order_json).status == "pending"
    assert db.query(GiftCertificate).count() == 0
    assert db.query(GiftPaymentEvent).count() == 0


def test_failed_and_expired_orders_never_issue_certificates(client, db, gift_world):
    failed_order = _ok(_purchase(client, gift_world, request_key="gift-failed-1"), "purchase")
    failed = _ok(_sandbox(client, failed_order, "failed"), "failed payment")
    assert failed["status"] == "failed"
    assert failed["certificate"] is None
    assert failed["checkout_url"] is None

    open_order = _ok(_purchase(client, gift_world, request_key="gift-late-1"), "second purchase")
    db.expire_all()
    row = _order(db, open_order)
    row.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    expired = _ok(
        client.get(
            f"/public/gifts/orders/{open_order['public_ref']}",
            headers={"X-Gift-Token": _token(open_order)},
        ),
        "expired order",
    )
    assert expired["status"] == "expired"
    late = _ok(
        _webhook(client, row, "payment.paid", "evt-gift-late-paid"), "late paid"
    )
    assert late["status"] == "reconcile"
    db.expire_all()
    assert db.query(GiftCertificate).count() == 0


def test_money_certificate_partial_redemption_is_capped_and_idempotent(client, db, gift_world):
    _order_json, paid = _pay(client, gift_world)
    visit = gift_world["visit"]
    body = {
        "code": paid["certificate"]["code"],
        "visit_id": visit["id"],
        "amount": 20,
        "request_key": "redeem-money-1",
        "reason": "Birthday gift",
    }
    first = _ok(
        client.post(
            "/businesses/me/gift-certificates/redeem",
            json=body,
            headers=gift_world["headers"],
        ),
        "partial redemption",
    )
    assert first["applied_amount"] == pytest.approx(20)
    assert first["applied_visits"] == 0
    assert first["certificate"]["money_balance"] == pytest.approx(30)

    replay = _ok(
        client.post(
            "/businesses/me/gift-certificates/redeem",
            json=body,
            headers=gift_world["headers"],
        ),
        "replayed redemption",
    )
    assert replay["entry_id"] == first["entry_id"]
    assert replay["certificate"]["money_balance"] == pytest.approx(30)
    db.expire_all()
    assert (
        db.query(GiftLedgerEntry)
        .filter(GiftLedgerEntry.entry_type == "redeemed")
        .count()
        == 1
    )

    conflict = client.post(
        "/businesses/me/gift-certificates/redeem",
        json={**body, "amount": 10},
        headers=gift_world["headers"],
    )
    assert conflict.status_code == 409, conflict.text

    too_much = client.post(
        "/businesses/me/gift-certificates/redeem",
        json={**body, "amount": 40, "request_key": "redeem-money-2"},
        headers=gift_world["headers"],
    )
    assert too_much.status_code == 409, too_much.text

    cheap_visit = _new_visit(client, gift_world, price=10)
    over_price = client.post(
        "/businesses/me/gift-certificates/redeem",
        json={
            **body,
            "visit_id": cheap_visit["id"],
            "amount": 20,
            "request_key": "redeem-money-3",
        },
        headers=gift_world["headers"],
    )
    assert over_price.status_code == 409, over_price.text
    capped = _ok(
        client.post(
            "/businesses/me/gift-certificates/redeem",
            json={
                **body,
                "visit_id": cheap_visit["id"],
                "amount": 10,
                "request_key": "redeem-money-4",
            },
            headers=gift_world["headers"],
        ),
        "capped at visit price",
    )
    assert capped["certificate"]["money_balance"] == pytest.approx(20)

    exhausted = _ok(
        client.post(
            "/businesses/me/gift-certificates/redeem",
            json={
                **body,
                "amount": 20,
                "request_key": "redeem-money-5",
            },
            headers=gift_world["headers"],
        ),
        "exhaust",
    )
    assert exhausted["certificate"]["money_balance"] == pytest.approx(0)
    assert exhausted["certificate"]["status"] == "exhausted"

    late_replay = _ok(
        client.post(
            "/businesses/me/gift-certificates/redeem",
            json=body,
            headers=gift_world["headers"],
        ),
        "replay after exhaustion",
    )
    assert late_replay["entry_id"] == first["entry_id"]
    assert late_replay["certificate"]["money_balance"] == pytest.approx(0)

    refused = client.post(
        "/businesses/me/gift-certificates/redeem",
        json={**body, "amount": 1, "request_key": "redeem-money-6"},
        headers=gift_world["headers"],
    )
    assert refused.status_code == 409, refused.text


def test_package_redemption_spends_visits_and_respects_service_scope(client, gift_world):
    _order_json, paid = _pay(
        client,
        gift_world,
        offer_id=gift_world["package_offer"]["id"],
        request_key="gift-package-1",
    )
    assert paid["certificate"]["visits_remaining"] == 3
    code = paid["certificate"]["code"]
    base = {
        "code": code,
        "visit_id": gift_world["visit"]["id"],
        "request_key": "redeem-visit-1",
    }
    first = _ok(
        client.post(
            "/businesses/me/gift-certificates/redeem",
            json=base,
            headers=gift_world["headers"],
        ),
        "package redemption",
    )
    assert first["applied_visits"] == 1
    assert first["certificate"]["visits_remaining"] == 2
    assert first["applied_amount"] == pytest.approx(0)

    replay = _ok(
        client.post(
            "/businesses/me/gift-certificates/redeem",
            json=base,
            headers=gift_world["headers"],
        ),
        "package replay",
    )
    assert replay["entry_id"] == first["entry_id"]
    assert replay["certificate"]["visits_remaining"] == 2

    other_service = _ok(
        client.post(
            "/services/",
            json={
                "name": "Alpha Nails",
                "price": 30,
                "duration_minutes": 30,
                "specialist_id": gift_world["owner"].id,
            },
            headers=gift_world["headers"],
        ),
        "second service",
    )
    other_visit = _new_visit(client, gift_world, other_service["id"], price=30)
    out_of_scope = client.post(
        "/businesses/me/gift-certificates/redeem",
        json={**base, "visit_id": other_visit["id"], "request_key": "redeem-visit-2"},
        headers=gift_world["headers"],
    )
    assert out_of_scope.status_code == 409, out_of_scope.text

    for index in range(2, 4):
        result = _ok(
            client.post(
                "/businesses/me/gift-certificates/redeem",
                json={**base, "request_key": f"redeem-visit-{index}"},
                headers=gift_world["headers"],
            ),
            f"visit {index}",
        )
    assert result["certificate"]["visits_remaining"] == 0
    assert result["certificate"]["status"] == "exhausted"
    refused = client.post(
        "/businesses/me/gift-certificates/redeem",
        json={**base, "request_key": "redeem-visit-6"},
        headers=gift_world["headers"],
    )
    assert refused.status_code == 409, refused.text


def test_reversal_returns_a_redemption_once_and_keeps_history(client, db, gift_world):
    _order_json, paid = _pay(client, gift_world)
    code = paid["certificate"]["code"]
    redeemed = _ok(
        client.post(
            "/businesses/me/gift-certificates/redeem",
            json={
                "code": code,
                "visit_id": gift_world["visit"]["id"],
                "amount": 20,
                "request_key": "redeem-reverse-1",
            },
            headers=gift_world["headers"],
        ),
        "redeem",
    )
    certificate = _certificate(db, paid)
    path = f"/businesses/me/gift-certificates/{certificate.id}/reversals"
    payload = {
        "redemption_entry_id": redeemed["entry_id"],
        "request_key": "reverse-gift-1",
        "reason": "Visit cancelled",
    }
    first = _ok(client.post(path, json=payload, headers=gift_world["headers"]), "reversal")
    assert first["restored_amount"] == pytest.approx(20)
    assert first["certificate"]["money_balance"] == pytest.approx(50)
    assert first["certificate"]["status"] == "active"

    replay = _ok(client.post(path, json=payload, headers=gift_world["headers"]), "replay")
    assert replay["entry_id"] == first["entry_id"]
    assert replay["certificate"]["money_balance"] == pytest.approx(50)
    detail = _ok(
        client.get(
            f"/businesses/me/gift-certificates/{certificate.id}",
            headers=gift_world["headers"],
        ),
        "detail",
    )
    reversed_entry = next(row for row in detail["ledger"] if row["entry_type"] == "reversed")
    assert reversed_entry["redeems_entry_id"] == redeemed["entry_id"]
    db.expire_all()
    entries = (
        db.query(GiftLedgerEntry)
        .filter(GiftLedgerEntry.certificate_id == certificate.id)
        .order_by(GiftLedgerEntry.id)
        .all()
    )
    assert [row.entry_type for row in entries] == ["issued", "redeemed", "reversed"]

    unknown = client.post(
        path,
        json={**payload, "redemption_entry_id": 999999, "request_key": "reverse-gift-2"},
        headers=gift_world["headers"],
    )
    assert unknown.status_code == 404, unknown.text


def test_foreign_certificates_are_invisible(client, gift_world, make_user, headers_for):
    _order_json, paid = _pay(
        client,
        gift_world,
        recipient_name="Secret Recipient",
        recipient_email="secret.recipient@example.com",
    )
    code = paid["certificate"]["code"]
    bravo = build_tenant(client, make_user, headers_for, "Bravo", "+42070022")

    lookup = client.get(
        "/businesses/me/gift-certificates/lookup",
        params={"code": code},
        headers=bravo["headers"],
    )
    assert lookup.status_code == 404, lookup.text
    assert "Secret Recipient" not in lookup.text
    assert "secret.recipient@example.com" not in lookup.text
    redeem = client.post(
        "/businesses/me/gift-certificates/redeem",
        json={
            "code": code,
            "visit_id": bravo["visit"]["id"],
            "request_key": "foreign-redeem-1",
        },
        headers=bravo["headers"],
    )
    assert redeem.status_code == 404, redeem.text
    listed = _ok(
        client.get("/businesses/me/gift-certificates", headers=bravo["headers"]),
        "bravo certificates",
    )
    assert listed == []


def test_certificate_expiry_writes_off_once_and_blocks_redemption(client, db, gift_world):
    _order_json, paid = _pay(client, gift_world)
    certificate = _certificate(db, paid)
    certificate.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()

    first = _ok(
        client.get(
            "/businesses/me/gift-certificates/lookup",
            params={"code": paid["certificate"]["code"]},
            headers=gift_world["headers"],
        ),
        "expired lookup",
    )
    assert first["status"] == "expired"
    assert first["money_balance"] == pytest.approx(0)
    second = _ok(
        client.get(
            "/businesses/me/gift-certificates/lookup",
            params={"code": paid["certificate"]["code"]},
            headers=gift_world["headers"],
        ),
        "second lookup",
    )
    assert second["money_balance"] == pytest.approx(0)
    db.expire_all()
    expired = (
        db.query(GiftLedgerEntry)
        .filter(
            GiftLedgerEntry.certificate_id == certificate.id,
            GiftLedgerEntry.entry_type == "expired",
        )
        .all()
    )
    assert len(expired) == 1
    assert float(expired[0].amount_delta) == pytest.approx(-50)

    refused = client.post(
        "/businesses/me/gift-certificates/redeem",
        json={
            "code": paid["certificate"]["code"],
            "visit_id": gift_world["visit"]["id"],
            "amount": 10,
            "request_key": "expired-redeem-1",
        },
        headers=gift_world["headers"],
    )
    assert refused.status_code == 409, refused.text


def test_parallel_redemptions_never_make_the_balance_negative(client, db, gift_world):
    _order_json, paid = _pay(client, gift_world, request_key="gift-concurrent-1")
    body = {
        "code": paid["certificate"]["code"],
        "visit_id": gift_world["visit"]["id"],
        "amount": 40,
    }

    def redeem(key):
        return client.post(
            "/businesses/me/gift-certificates/redeem",
            json={**body, "request_key": key},
            headers=gift_world["headers"],
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(redeem, "concurrent-gift-a")
        second = pool.submit(redeem, "concurrent-gift-b")
        results = [first.result(), second.result()]

    codes = sorted(result.status_code for result in results)
    assert codes == [200, 409], [result.text for result in results]
    certificate = _certificate(db, paid)
    balance = (
        db.query(GiftLedgerEntry)
        .filter(GiftLedgerEntry.certificate_id == certificate.id)
        .all()
    )
    total = sum(float(row.amount_delta) for row in balance)
    assert total == pytest.approx(10)
    assert total >= 0


def test_purchase_is_off_without_a_configured_provider(client, gift_world, monkeypatch):
    from app.api.core.config import settings

    monkeypatch.setattr(settings, "service_payment_provider", "")
    shop = _shop(client, gift_world)
    assert shop["payments_available"] is False
    refused = _purchase(client, gift_world, request_key="gift-no-provider")
    assert refused.status_code == 503, refused.text


def test_browser_preflight_allows_the_gift_token(client):
    # checkout reads its order with a token header, not Authorization
    response = client.options(
        "/public/gifts/orders/some-ref",
        headers={
            "Origin": "http://localhost:3170",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-Gift-Token",
        },
    )
    assert response.status_code == 200, response.text
    assert "x-gift-token" in response.headers["access-control-allow-headers"].lower()


def test_pilot_gift_shop_and_orders_close_with_the_salon(client, db, gift_world, monkeypatch):
    from app.api.core.config import settings
    from tests.test_pilot_access import expire
    order, _paid = _pay(client, gift_world)
    monkeypatch.setattr(settings, "pilot_mode_enabled", True)
    shop = lambda: client.get(f"/public/gifts/{gift_world['slug']}")
    order_state = lambda: client.get(f"/public/gifts/orders/{order['public_ref']}", headers={"X-Gift-Token": _token(order)})
    assert shop().status_code == 200
    assert order_state().status_code == 200
    expire(db, gift_world["owner"], 3)
    assert shop().status_code == 402
    assert order_state().status_code == 402
