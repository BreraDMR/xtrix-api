"""Billing lifecycle against the real database and mocked Stripe SDK calls only."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
import stripe

from app.api.core import stripe_billing
from app.api.core.clock import utcnow
from app.api.core.config import settings
from app.api.crud import crud_subscription
from app.db.models.subscription import Subscription
from app.db.models.transaction import Transaction


@pytest.fixture
def billing(client, db, make_user, headers_for, monkeypatch):
    owner = make_user("business_owner")
    plan = crud_subscription.get_plan_by_code(db, "admin_up_to_3_employees")
    end = int((utcnow() + timedelta(days=30)).replace(tzinfo=timezone.utc).timestamp())
    remote = {"id": "sub_test", "status": "active", "cancel_at_period_end": False,
              "metadata": {"user_id": str(owner.id), "plan_id": str(plan.id)},
              "items": {"data": [{"current_period_end": end}]}}
    monkeypatch.setattr(settings, "stripe_api_key", "sk_test_mock")
    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_mock")
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda payload, *_: stripe.Event.construct_from(
        json.loads(payload), "sk_test_mock"))
    calls = []

    def retrieve(subscription_id):
        calls.append(("retrieve", subscription_id))
        assert subscription_id == remote["id"]
        return stripe.StripeObject.construct_from(copy.deepcopy(remote), "sk_test_mock")

    def unexpected(*args, **kwargs):
        raise AssertionError("Unexpected Stripe request")

    monkeypatch.setattr(stripe.Subscription, "retrieve", retrieve)
    for target in (stripe.Subscription, stripe.checkout.Session):
        monkeypatch.setattr(target, "list", lambda **_: stripe.StripeObject.construct_from(
            {"data": [], "has_more": False}, "sk_test_mock"))
    for target, method in [(stripe.Subscription, "modify"), (stripe.Subscription, "cancel"),
                           (stripe.Customer, "list"), (stripe.Customer, "create"),
                           (stripe.checkout.Session, "create")]:
        monkeypatch.setattr(target, method, unexpected)

    def send(kind, obj=None):
        return client.post("/subscriptions/webhooks/stripe", json={
            "id": "evt_test", "type": kind, "data": {"object": obj or remote}})

    return {"owner": owner, "plan": plan, "remote": remote, "send": send,
            "headers": headers_for(owner), "calls": calls}


def stripe_sub(db):
    db.expire_all()
    return db.query(Subscription).filter_by(provider="stripe", external_id="sub_test").one()


def invoice(**overrides):
    return {"id": "in_test", "parent": {"subscription_details": {"subscription": "sub_test"}},
            "amount_paid": 6200, "amount_due": 6200, "currency": "eur", **overrides}


@pytest.mark.parametrize("price", [0, -1, 0.001])
def test_checkout_requires_a_paid_nontrial_plan(client, db, billing, price):
    trial = crud_subscription.get_active_subscription(db, billing["owner"].id)
    r = client.post(f"/subscriptions/checkout/create-session?plan_id={trial.plan_id}", headers=billing["headers"])
    assert r.status_code == 400
    trial.plan.price = 10
    db.commit()
    r = client.post(f"/subscriptions/checkout/create-session?plan_id={trial.plan_id}", headers=billing["headers"])
    assert r.status_code == 400
    plan = billing["plan"]
    plan.price = price
    db.commit()
    r = client.post(f"/subscriptions/checkout/create-session?plan_id={plan.id}", headers=billing["headers"])
    assert r.status_code == 400


def test_checkout_copies_metadata_to_subscription_and_keeps_exact_cents(client, db, billing, monkeypatch):
    plan = billing["plan"]
    plan.price = 19.99
    db.commit()
    monkeypatch.setattr(stripe.Customer, "list", lambda **_: stripe.StripeObject.construct_from(
        {"data": [{"id": "cus_test", "metadata": {"user_id": str(billing["owner"].id)}}]}, "sk_test_mock"))
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return stripe.StripeObject.construct_from({"id": "cs_test", "url": "https://example.test/pay"}, "sk_test_mock")

    monkeypatch.setattr(stripe.checkout.Session, "create", create)
    r = client.post(f"/subscriptions/checkout/create-session?plan_id={plan.id}", headers=billing["headers"])
    assert r.status_code == 200, r.text
    assert captured["metadata"] == captured["subscription_data"]["metadata"]
    assert {k: captured["metadata"][k] for k in ("user_id", "plan_id")} == billing["remote"]["metadata"]
    assert captured["line_items"][0]["price_data"]["unit_amount"] == 1999


def test_checkout_session_accepts_sdk16_metadata_and_reports_retryable_failures(client, billing, monkeypatch):
    session = stripe.checkout.Session.construct_from({"id": "cs_test", "payment_status": "paid",
        "subscription": "sub_test", "metadata": billing["remote"]["metadata"]}, "sk_test_mock")
    monkeypatch.setattr(stripe.checkout.Session, "retrieve", lambda _: session)
    assert client.get("/subscriptions/checkout/session/cs_test", headers=billing["headers"]).status_code == 200

    def unavailable(*_):
        raise stripe.APIConnectionError("mock outage")

    monkeypatch.setattr(stripe.checkout.Session, "retrieve", unavailable)
    assert client.get("/subscriptions/checkout/session/cs_test", headers=billing["headers"]).status_code == 503


@pytest.mark.parametrize("status", ["active", "pending_cancellation", "incomplete", "past_due", "unpaid", "paused"])
def test_checkout_cannot_start_another_live_stripe_subscription(client, db, billing, status):
    billing["remote"]["status"] = status
    assert billing["send"]("customer.subscription.created").status_code == 200
    r = client.post(f"/subscriptions/checkout/create-session?plan_id={billing['plan'].id}", headers=billing["headers"])
    assert r.status_code == 409, r.text


@pytest.mark.parametrize("change_plan", [False, True])
def test_trial_reactivation_cannot_extend_expired_access_or_switch_to_a_paid_plan(client, db, billing, change_plan):
    trial = crud_subscription.get_active_subscription(db, billing["owner"].id)
    trial.status, trial.cancel_at_period_end = "pending_cancellation", True
    if not change_plan:
        trial.current_period_end = utcnow() - timedelta(days=1)
    end, plan_id = trial.current_period_end, trial.plan_id
    db.commit()
    r = client.post(f"/subscriptions/{trial.id}/reactivate", headers=billing["headers"],
                    json={"plan_id": billing["plan"].id} if change_plan else {})
    assert r.status_code == 400, r.text
    db.refresh(trial)
    assert trial.current_period_end == end and trial.plan_id == plan_id and trial.status == "pending_cancellation"


def test_trial_reactivation_with_same_plan_cannot_reset_the_billing_period(client, db, billing):
    trial = crud_subscription.get_active_subscription(db, billing["owner"].id)
    trial.status, trial.cancel_at_period_end = "pending_cancellation", True
    trial.current_period_end = utcnow() + timedelta(hours=2)
    end = trial.current_period_end
    db.commit()
    r = client.post(f"/subscriptions/{trial.id}/reactivate", headers=billing["headers"],
                    json={"plan_id": trial.plan_id})
    assert r.status_code == 200, r.text
    db.refresh(trial)
    assert trial.status == "active" and trial.current_period_end == end


def test_subscription_created_before_checkout_and_duplicate_invoices_are_idempotent(db, billing):
    send = billing["send"]
    assert send("customer.subscription.created").status_code == 200
    checkout = {"subscription": "sub_test", "payment_status": "paid", "metadata": billing["remote"]["metadata"]}
    for kind, obj in [("checkout.session.completed", checkout), ("invoice.payment_succeeded", invoice()),
                      ("invoice.payment_succeeded", invoice()), ("invoice.paid", invoice()),
                      ("customer.subscription.created", billing["remote"])]:
        r = send(kind, obj)
        assert r.status_code == 200, r.text
    sub = stripe_sub(db)
    assert sub.status == "active" and not sub.is_trial
    assert sub.current_period_end == datetime.fromtimestamp(
        billing["remote"]["items"]["data"][0]["current_period_end"], timezone.utc).replace(tzinfo=None)
    assert db.query(Subscription).filter_by(provider="stripe").count() == 1
    [tx] = db.query(Transaction).filter_by(provider="stripe").all()
    assert tx.external_transaction_id == "in_test" and tx.amount == 62 and tx.status == "succeeded"
    assert sub.last_transaction_id == tx.id


def test_invoice_before_subscription_creation_recovers_metadata_and_payment(db, billing):
    assert billing["send"]("invoice.payment_succeeded", invoice()).status_code == 200
    assert stripe_sub(db).status == "active"
    assert db.query(Transaction).filter_by(provider="stripe").count() == 1


def test_paid_subscription_replaces_longer_trial_and_past_due_cannot_restore_trial_access(db, billing):
    trial = crud_subscription.get_active_subscription(db, billing["owner"].id)
    trial.current_period_end = trial.trial_end = utcnow() + timedelta(days=90)
    db.commit()
    assert billing["send"]("customer.subscription.created").status_code == 200
    db.expire_all()
    paid = crud_subscription.get_active_subscription(db, billing["owner"].id)
    assert paid.provider == "stripe" and not paid.is_trial
    db.refresh(trial)
    assert trial.status == "expired" and trial.current_period_end <= utcnow()
    billing["remote"]["status"] = "past_due"
    assert billing["send"]("invoice.payment_failed", invoice()).status_code == 200
    db.expire_all()
    assert not crud_subscription.check_subscription_access(db, billing["owner"].id)[0]


def test_concurrent_first_invoice_deliveries_create_one_subscription_and_payment(db, billing):
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: billing["send"]("invoice.payment_succeeded", invoice()), range(2)))
    assert [r.status_code for r in responses] == [200, 200]
    db.expire_all()
    assert db.query(Subscription).filter_by(provider="stripe").count() == 1
    assert db.query(Transaction).filter_by(provider="stripe").count() == 1


def test_old_subscription_event_uses_current_stripe_state_and_unknown_events_do_nothing(db, billing):
    assert billing["send"]("customer.subscription.updated", {"id": "sub_test", "status": "past_due"}).status_code == 200
    assert stripe_sub(db).status == "active"
    before = len(billing["calls"])
    assert billing["send"]("unrelated.event", {"id": "sub_test", "status": "cancelled"}).status_code == 200
    assert stripe_sub(db).status == "active" and len(billing["calls"]) == before


def test_invoice_retry_upgrades_failed_payment_but_old_failure_cannot_undo_success(db, billing):
    for kind in ["invoice.payment_failed", "invoice.payment_failed", "invoice.payment_succeeded", "invoice.payment_failed"]:
        assert billing["send"](kind, invoice()).status_code == 200
    db.expire_all()
    [tx] = db.query(Transaction).filter_by(provider="stripe").all()
    assert tx.status == "succeeded" and tx.amount == 62


def test_stripe_failure_is_retryable_and_does_not_acknowledge_or_create_a_subscription(db, billing, monkeypatch):
    retrieve = stripe.Subscription.retrieve

    def unavailable(*_):
        raise stripe.APIConnectionError("mock outage")

    monkeypatch.setattr(stripe.Subscription, "retrieve", unavailable)
    assert billing["send"]("customer.subscription.created").status_code == 503
    assert db.query(Subscription).filter_by(provider="stripe").count() == 0
    monkeypatch.setattr(stripe.Subscription, "retrieve", retrieve)
    assert billing["send"]("customer.subscription.created").status_code == 200
    assert stripe_sub(db).status == "active"


def test_webhook_rolls_back_subscription_when_recording_payment_fails(db, billing, monkeypatch):
    trial = crud_subscription.get_active_subscription(db, billing["owner"].id)
    def broken(*args, **kwargs):
        raise RuntimeError("mock database failure")

    monkeypatch.setattr(stripe_billing, "record_invoice", broken)
    with pytest.raises(RuntimeError, match="mock database failure"):
        billing["send"]("invoice.payment_succeeded", invoice())
    db.expire_all()
    assert db.query(Subscription).filter_by(provider="stripe").count() == 0
    assert db.query(Transaction).filter_by(provider="stripe").count() == 0
    db.refresh(trial)
    assert trial.status == "active"


def test_missing_item_period_cannot_fabricate_free_access(db, billing):
    billing["remote"]["items"]["data"] = []
    billing["remote"]["current_period_end"] = int((utcnow() + timedelta(days=90)).replace(tzinfo=timezone.utc).timestamp())
    assert billing["send"]("customer.subscription.created").status_code == 503
    assert db.query(Subscription).filter_by(provider="stripe").count() == 0


@pytest.mark.parametrize("immediate", [False, True])
def test_cancel_calls_stripe_before_changing_local_state(client, db, billing, monkeypatch, immediate):
    assert billing["send"]("customer.subscription.created").status_code == 200
    sub = stripe_sub(db)
    calls = []

    def cancel(subscription_id, **kwargs):
        db.refresh(sub)
        assert sub.status == "active" and not sub.cancel_at_period_end
        calls.append((subscription_id, kwargs))
        billing["remote"].update(status="canceled" if immediate else "active", cancel_at_period_end=not immediate)
        return copy.deepcopy(billing["remote"])

    monkeypatch.setattr(stripe.Subscription, "cancel" if immediate else "modify", cancel)
    r = client.post(f"/subscriptions/{sub.id}/cancel", json={"cancel_at_period_end": not immediate},
                    headers=billing["headers"])
    assert r.status_code == 200, r.text
    assert calls == [("sub_test", {} if immediate else {"cancel_at_period_end": True})]
    assert stripe_sub(db).status == ("cancelled" if immediate else "pending_cancellation")


def test_cancel_stripe_failure_keeps_local_state(client, db, billing, monkeypatch):
    billing["send"]("customer.subscription.created")
    sub = stripe_sub(db)

    def unavailable(*args, **kwargs):
        raise stripe.APIConnectionError("mock outage")

    monkeypatch.setattr(stripe.Subscription, "modify", unavailable)
    r = client.post(f"/subscriptions/{sub.id}/cancel", json={}, headers=billing["headers"])
    assert r.status_code == 503
    assert stripe_sub(db).status == "active" and not sub.cancel_at_period_end


@pytest.mark.parametrize("status,expired,change_plan", [
    ("cancelled", False, False), ("expired", True, False), ("active", False, False),
    ("pending_cancellation", True, False), ("pending_cancellation", False, True),
])
def test_reactivation_cannot_buy_time_or_change_plan(client, db, billing, status, expired, change_plan):
    billing["send"]("customer.subscription.created")
    sub = stripe_sub(db)
    sub.status, sub.cancel_at_period_end = status, True
    if expired:
        sub.current_period_end = utcnow() - timedelta(days=1)
    end = sub.current_period_end
    db.commit()
    before = len(billing["calls"])
    r = client.post(f"/subscriptions/{sub.id}/reactivate", headers=billing["headers"],
                    json={"plan_id": sub.plan_id + 1} if change_plan else {})
    assert r.status_code == 400, r.text
    assert len(billing["calls"]) == before
    assert stripe_sub(db).current_period_end == end and sub.status == status


def test_reactivation_only_removes_pending_cancellation_without_extending_period(client, db, billing, monkeypatch):
    billing["remote"]["cancel_at_period_end"] = True
    billing["send"]("customer.subscription.created")
    sub = stripe_sub(db)
    end = sub.current_period_end

    def modify(subscription_id, **kwargs):
        assert subscription_id == "sub_test" and kwargs == {"cancel_at_period_end": False}
        billing["remote"]["cancel_at_period_end"] = False
        return copy.deepcopy(billing["remote"])

    monkeypatch.setattr(stripe.Subscription, "modify", modify)
    r = client.post(f"/subscriptions/{sub.id}/reactivate", json={}, headers=billing["headers"])
    assert r.status_code == 200, r.text
    assert stripe_sub(db).status == "active" and not sub.cancel_at_period_end
    assert sub.current_period_end == end and sub.reactivated_at is not None


@pytest.mark.parametrize("state", ["canceled", "expired", "not_pending"])
def test_reactivation_checks_current_stripe_state_before_modification(client, db, billing, state):
    billing["remote"]["cancel_at_period_end"] = True
    billing["send"]("customer.subscription.created")
    sub = stripe_sub(db)
    if state == "canceled":
        billing["remote"]["status"] = "canceled"
    elif state == "expired":
        billing["remote"]["items"]["data"][0]["current_period_end"] = 1
    else:
        billing["remote"]["cancel_at_period_end"] = False
    r = client.post(f"/subscriptions/{sub.id}/reactivate", json={}, headers=billing["headers"])
    assert r.status_code == 400, r.text
    assert stripe_sub(db).status == "pending_cancellation" and sub.cancel_at_period_end


def test_reactivation_stripe_failure_keeps_local_pending_cancellation(client, db, billing, monkeypatch):
    billing["remote"]["cancel_at_period_end"] = True
    billing["send"]("customer.subscription.created")
    sub = stripe_sub(db)

    def unavailable(*args, **kwargs):
        raise stripe.APIConnectionError("mock outage")

    monkeypatch.setattr(stripe.Subscription, "modify", unavailable)
    r = client.post(f"/subscriptions/{sub.id}/reactivate", json={}, headers=billing["headers"])
    assert r.status_code == 503
    assert stripe_sub(db).status == "pending_cancellation" and sub.cancel_at_period_end


def test_missing_api_key_refuses_checkout_webhook_and_stripe_management(client, db, billing, monkeypatch):
    billing["send"]("customer.subscription.created")
    sub = stripe_sub(db)
    monkeypatch.setattr(settings, "stripe_api_key", "")
    assert billing["send"]("customer.subscription.updated").status_code == 503
    for path in [f"/subscriptions/{sub.id}/cancel", f"/subscriptions/{sub.id}/reactivate",
                 f"/subscriptions/checkout/create-session?plan_id={billing['plan'].id}"]:
        assert client.post(path, json={}, headers=billing["headers"]).status_code == 503
    assert client.get("/subscriptions/checkout/session/cs_test", headers=billing["headers"]).status_code == 503


def test_missing_webhook_secret_is_a_configuration_failure(billing, monkeypatch):
    monkeypatch.setattr(settings, "stripe_webhook_secret", "")
    assert billing["send"]("customer.subscription.created").status_code == 503
