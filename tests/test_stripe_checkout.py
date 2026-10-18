"""Stripe is an in-memory fake; PostgreSQL supplies the real request locks and durable state."""
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, timezone
from threading import Event

import pytest
import stripe

from app.api.core import stripe_checkout
from app.api.core.clock import utcnow
from app.api.core.config import settings
from app.api.crud import crud_subscription
from app.db.models.stripe_checkout import StripeCheckoutState
from app.db.models.subscription import Subscription


def sdk(data):
    return stripe.StripeObject.construct_from(copy.deepcopy(data), "sk_test_mock")


@pytest.fixture
def checkout(client, db, make_user, headers_for, monkeypatch):
    user = make_user("business_owner")
    plans = [crud_subscription.get_plan_by_code(db, code) for code in (
        "admin_up_to_3_employees", "admin_up_to_7_employees")]
    headers = headers_for(user)
    remote = {"customers": {}, "sessions": {}, "subscriptions": {}, "keys": {}, "creates": [],
              "customer_creates": 0, "expires": [], "hide_sessions": False,
              "lose_create": False, "lose_customer": False, "pay_on_expire": False}
    monkeypatch.setattr(settings, "stripe_api_key", "sk_test_mock")

    def customer_list(**params):
        return sdk({"data": list(remote["customers"].values())})

    def customer_create(**params):
        remote["customer_creates"] += 1
        cid = "cus_" + str(remote["customer_creates"])
        item = {"id": cid, **params}
        remote["customers"][cid] = item
        if remote["lose_customer"]:
            remote["lose_customer"] = False
            raise stripe.APIConnectionError("mock lost customer response")
        return sdk(item)

    def sessions_list(**params):
        rows = [] if remote["hide_sessions"] else [s for s in remote["sessions"].values()
            if s["customer"] == params["customer"]]
        return sdk({"data": rows})

    def sessions_create(**params):
        key = params.pop("idempotency_key")
        if key in remote["keys"]:
            old_params, sid = remote["keys"][key]
            assert old_params == params, "An idempotency retry must keep its original payload"
            return sdk(remote["sessions"][sid])
        remote["creates"].append(copy.deepcopy(params))
        sid = "cs_" + str(len(remote["creates"]))
        item = {"id": sid, "status": "open", "url": "https://example.test/" + sid,
                "mode": "subscription", "subscription": None, "payment_status": "unpaid", **params}
        remote["keys"][key] = (copy.deepcopy(params), sid)
        remote["sessions"][sid] = item
        if remote["lose_create"]:
            remote["lose_create"] = False
            raise stripe.APIConnectionError("mock lost checkout response")
        return sdk(item)

    def pay(sid):
        session = remote["sessions"][sid]
        subscription_id = "sub_" + sid
        session.update(status="complete", payment_status="paid", subscription=subscription_id)
        remote["subscriptions"][subscription_id] = {
            "id": subscription_id, "status": "active", "customer": session["customer"],
            "metadata": session["subscription_data"]["metadata"], "cancel_at_period_end": False,
            "items": {"data": [{"current_period_end": int(
                (utcnow() + timedelta(days=30)).replace(tzinfo=timezone.utc).timestamp())}]} }

    def expire(sid):
        if remote["pay_on_expire"]:
            pay(sid)
        item = remote["sessions"][sid]
        if item["status"] != "open":
            raise stripe.InvalidRequestError("Session is no longer open", "id")
        item["status"] = "expired"
        remote["expires"].append(sid)
        return sdk(item)

    monkeypatch.setattr(stripe.Customer, "list", customer_list)
    monkeypatch.setattr(stripe.Customer, "create", customer_create)
    monkeypatch.setattr(stripe.checkout.Session, "list", sessions_list)
    monkeypatch.setattr(stripe.checkout.Session, "create", sessions_create)
    monkeypatch.setattr(stripe.checkout.Session, "retrieve", lambda sid: sdk(remote["sessions"][sid]))
    monkeypatch.setattr(stripe.checkout.Session, "expire", expire)
    monkeypatch.setattr(stripe.Subscription, "list", lambda **params: sdk({"data": [s for s in
        remote["subscriptions"].values() if s["customer"] == params["customer"]]}))
    monkeypatch.setattr(stripe.Subscription, "retrieve", lambda sid: sdk(remote["subscriptions"][sid]))

    def post(plan=None):
        return client.post(f"/subscriptions/checkout/create-session?plan_id={plan or plans[0].id}", headers=headers)

    return {"user": user, "plans": plans, "remote": remote, "post": post, "pay": pay}


def test_repeated_checkout_reuses_open_session_and_stable_customer(db, checkout):
    first, second = checkout["post"](), checkout["post"]()
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(checkout["remote"]["creates"]) == checkout["remote"]["customer_creates"] == 1
    state = db.get(StripeCheckoutState, checkout["user"].id)
    assert state.customer_id == "cus_1" and state.session_id == first.json()["session_id"]


def test_parallel_checkout_requests_share_one_payable_session(checkout, monkeypatch):
    entered, release = Event(), Event()
    create = stripe.checkout.Session.create

    def delayed(**params):
        entered.set()
        assert release.wait(10)
        return create(**params)

    monkeypatch.setattr(stripe.checkout.Session, "create", delayed)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(checkout["post"])
        assert entered.wait(10)
        second = pool.submit(checkout["post"])
        release.set()
        responses = [first.result(), second.result()]
    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    assert len(checkout["remote"]["creates"]) == checkout["remote"]["customer_creates"] == 1


@pytest.mark.parametrize("hide_sessions", [False, True])
def test_lost_create_response_retries_same_attempt_without_duplicate_session(checkout, hide_sessions):
    checkout["remote"]["lose_create"] = True
    assert checkout["post"]().status_code == 503
    checkout["remote"]["hide_sessions"] = hide_sessions
    response = checkout["post"]()
    assert response.status_code == 200, response.text
    assert response.json()["session_id"] == "cs_1"
    assert len(checkout["remote"]["creates"]) == 1


def test_lost_customer_response_recovers_customer_by_ownership(checkout):
    checkout["remote"]["lose_customer"] = True
    assert checkout["post"]().status_code == 503
    assert checkout["post"]().status_code == 200
    assert checkout["remote"]["customer_creates"] == 1


def test_customer_binding_survives_email_change_and_lost_checkout_response(db, checkout):
    checkout["remote"]["lose_create"] = True
    assert checkout["post"]().status_code == 503
    checkout["user"].email = "new-address@example.test"
    db.commit()
    response = checkout["post"]()
    assert response.status_code == 200, response.text
    assert response.json()["session_id"] == "cs_1"
    assert checkout["remote"]["customer_creates"] == 1


def test_plan_change_after_lost_response_expires_previous_session_before_creating_another(checkout):
    checkout["remote"]["lose_create"] = True
    assert checkout["post"]().status_code == 503
    checkout["remote"]["hide_sessions"] = True
    response = checkout["post"](checkout["plans"][1].id)
    assert response.status_code == 200, response.text
    assert checkout["remote"]["expires"] == ["cs_1"]
    assert [s["status"] for s in checkout["remote"]["sessions"].values()] == ["expired", "open"]


def test_parallel_different_plans_leave_only_one_open_session(checkout):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(checkout["post"], [p.id for p in checkout["plans"]]))
    assert [r.status_code for r in results] == [200, 200]
    assert sorted(s["status"] for s in checkout["remote"]["sessions"].values()) == ["expired", "open"]


def test_completed_payment_before_webhook_prevents_second_checkout_and_syncs_subscription(db, checkout):
    assert checkout["post"]().status_code == 200
    checkout["pay"]("cs_1")
    response = checkout["post"](checkout["plans"][1].id)
    assert response.status_code == 409, response.text
    assert len(checkout["remote"]["creates"]) == 1
    db.expire_all()
    assert db.query(Subscription).filter_by(provider="stripe").one().external_id == "sub_cs_1"


def test_payment_winning_expire_race_stops_new_session(checkout):
    assert checkout["post"]().status_code == 200
    checkout["remote"]["pay_on_expire"] = True
    assert checkout["post"](checkout["plans"][1].id).status_code == 503
    assert checkout["post"](checkout["plans"][1].id).status_code == 409
    assert len(checkout["remote"]["creates"]) == 1


def test_completed_session_blocks_even_when_subscription_list_lags(checkout, monkeypatch):
    checkout["post"]()
    checkout["pay"]("cs_1")
    monkeypatch.setattr(stripe.Subscription, "list", lambda **_: sdk({"data": []}))
    assert checkout["post"]().status_code == 409
    assert len(checkout["remote"]["creates"]) == 1


def test_old_unknown_attempt_never_reuses_pruned_idempotency_key(db, checkout):
    checkout["remote"]["lose_create"] = True
    checkout["post"]()
    state = db.get(StripeCheckoutState, checkout["user"].id)
    state.attempt_started_at = utcnow() - timedelta(days=2)
    db.commit()
    checkout["remote"]["hide_sessions"] = True
    checkout["remote"]["keys"].clear()
    assert checkout["post"]().status_code == 409
    assert len(checkout["remote"]["creates"]) == 1


def test_failed_response_persistence_recovers_from_saved_attempt(db, checkout, monkeypatch):
    persist = stripe_checkout._persist

    def fail_response(*args, **values):
        if values.get("session_id"):
            raise RuntimeError("mock lost database connection")
        return persist(*args, **values)

    monkeypatch.setattr(stripe_checkout, "_persist", fail_response)
    assert checkout["post"]().status_code == 500
    monkeypatch.setattr(stripe_checkout, "_persist", persist)
    assert checkout["post"]().json()["session_id"] == "cs_1"
    assert len(checkout["remote"]["creates"]) == 1


def test_customer_with_same_email_but_another_owner_is_never_reused(checkout):
    checkout["remote"]["customers"]["cus_other"] = {
        "id": "cus_other", "email": checkout["user"].email, "metadata": {"user_id": "999999"}}
    assert checkout["post"]().status_code == 200
    assert checkout["remote"]["sessions"]["cs_1"]["customer"] == "cus_1"


def test_existing_customer_binding_is_recovered_by_metadata_after_email_change(checkout):
    checkout["remote"]["customers"]["cus_legacy"] = {
        "id": "cus_legacy", "email": "old-address@example.test",
        "metadata": {"user_id": str(checkout["user"].id)}}
    assert checkout["post"]().status_code == 200
    assert checkout["remote"]["customer_creates"] == 0
    assert checkout["remote"]["sessions"]["cs_1"]["customer"] == "cus_legacy"


def test_request_payload_survives_price_change_before_idempotency_retry(db, checkout):
    checkout["remote"]["lose_create"] = True
    checkout["post"]()
    checkout["remote"]["hide_sessions"] = True
    checkout["plans"][0].price += 10
    db.commit()
    assert checkout["post"]().status_code == 200
    assert len(checkout["remote"]["creates"]) == 1


def test_customer_ownership_lookup_checks_all_pages(checkout, monkeypatch):
    calls = []

    def pages(**params):
        calls.append(params)
        if not params.get("starting_after"):
            return sdk({"data": [{"id": "cus_other", "metadata": {"user_id": "999"}}], "has_more": True})
        return sdk({"data": [{"id": "cus_legacy", "metadata": {"user_id": str(checkout["user"].id)}}]})

    monkeypatch.setattr(stripe.Customer, "list", pages)
    assert checkout["post"]().status_code == 200
    assert calls == [{"limit": 100}, {"limit": 100, "starting_after": "cus_other"}]
    assert checkout["remote"]["sessions"]["cs_1"]["customer"] == "cus_legacy"


def test_duplicate_legacy_open_sessions_are_expired_before_reuse(checkout):
    checkout["post"]()
    other = copy.deepcopy(checkout["remote"]["sessions"]["cs_1"])
    other.update(id="cs_duplicate", url="https://example.test/cs_duplicate")
    checkout["remote"]["sessions"][other["id"]] = other
    assert checkout["post"]().json()["session_id"] == "cs_1"
    assert checkout["remote"]["expires"] == ["cs_duplicate"]
