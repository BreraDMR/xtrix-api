"""Approved prices are public, but checkout still requires a mapped subscription plan."""
from datetime import timedelta, timezone

import pytest
from fastapi import HTTPException

from app.api.core import stripe_billing
from app.api.core.clock import utcnow
from app.api.core.entitlements_service import (
    build_checkout_payload_for_offer,
    resolve_offer_for_checkout,
)
from app.api.core.offer_catalog import OFFER_PLAN_CODES, OFFERS_DATA, get_offer_by_code, get_offer_catalog
from app.api.crud import crud_subscription


def offer(code: str) -> dict:
    return next(item for item in OFFERS_DATA if item["code"] == code)


def approve(monkeypatch, code: str, plan_code: str | None = None):
    monkeypatch.setitem(offer(code), "status", "approved")
    if plan_code:
        monkeypatch.setitem(OFFER_PLAN_CODES, code, plan_code)


def remote_subscription(owner_id: int, plan_id: int, **metadata):
    end = int((utcnow() + timedelta(days=30)).replace(tzinfo=timezone.utc).timestamp())
    return {
        "id": "sub_offer",
        "status": "active",
        "cancel_at_period_end": False,
        "metadata": {"user_id": str(owner_id), "plan_id": str(plan_id), **metadata},
        "items": {"data": [{"current_period_end": end}]},
    }


def test_approved_catalog_contains_the_exact_four_crm_and_ai_tiers():
    offers = get_offer_catalog().offers
    assert len(offers) == 8
    assert all(item.status == "approved" for item in offers)
    crm = [item for item in offers if item.product == "crm"]
    ai = [item for item in offers if item.product == "ai"]
    assert [(item.entitlements.max_monthly_visits, int(item.price)) for item in crm] == [(100, 200), (300, 400), (700, 700), (2000, 1200)]
    assert [(item.entitlements.daily_requests_limit, int(item.price)) for item in ai] == [(20, 200), (50, 400), (100, 700), (200, 1200)]
    assert all(item.entitlements.max_active_clients is None for item in crm)


def test_unapproved_prices_are_not_published_or_purchasable(monkeypatch):
    monkeypatch.setitem(offer("crm_starter_czk"), "status", "draft")
    assert get_offer_by_code("crm_starter_czk") is None
    assert "crm_starter_czk" not in [item.code for item in get_offer_catalog().offers]
    with pytest.raises(HTTPException) as exc:
        resolve_offer_for_checkout("crm_starter_czk")
    assert exc.value.status_code == 400


def test_approved_offer_without_a_plan_mapping_stays_closed(monkeypatch):
    approve(monkeypatch, "crm_starter_czk")
    with pytest.raises(HTTPException) as exc:
        resolve_offer_for_checkout("crm_starter_czk")
    assert exc.value.status_code == 400
    assert "plan" in exc.value.detail


def test_offer_payload_names_the_plan_that_will_be_provisioned(db, make_user, monkeypatch):
    approve(monkeypatch, "crm_starter_czk", "solo_basic")
    plan = crud_subscription.get_plan_by_code(db, "solo_basic")
    owner = make_user("business_owner")
    payload = build_checkout_payload_for_offer(
        owner, resolve_offer_for_checkout("crm_starter_czk"), "https://app.example", plan.id
    )
    expected = {"user_id": str(owner.id), "plan_id": str(plan.id),
                "offer_code": "crm_starter_czk", "product": "crm"}
    assert payload["metadata"] == expected
    assert payload["subscription_data"]["metadata"] == expected


def test_offer_webhook_provisions_the_paid_plan_and_expires_the_trial(db, make_user):
    owner = make_user("business_owner")
    plan = crud_subscription.get_plan_by_code(db, "solo_basic")
    sub = stripe_billing.sync_subscription(
        db, remote_subscription(owner.id, plan.id, offer_code="crm_starter_czk", product="crm")
    )
    db.commit()
    assert sub is not None
    assert (sub.user_id, sub.plan_id, sub.offer_code) == (owner.id, plan.id, "crm_starter_czk")
    assert sub.is_trial is False
    assert crud_subscription.get_active_subscription(db, owner.id).id == sub.id


def test_unknown_offer_in_webhook_is_rejected(db, make_user):
    owner = make_user("business_owner")
    plan = crud_subscription.get_plan_by_code(db, "solo_basic")
    with pytest.raises(HTTPException) as exc:
        stripe_billing.sync_subscription(
            db, remote_subscription(owner.id, plan.id, offer_code="not_a_real_offer")
        )
    assert exc.value.status_code == 400
