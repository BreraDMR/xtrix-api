"""One payable Checkout Session per user, even when Stripe's response gets lost."""
from datetime import timedelta
from uuid import uuid4

import stripe
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.core import stripe_billing
from app.api.core.clock import utcnow
from app.db.models.stripe_checkout import StripeCheckoutState
from app.db.models.subscription import Subscription


def _persist(db, user_id, **values):
    # Commit recovery data before the external call. The request's other connection
    # still holds the user advisory lock, including across this short commit.
    with Session(bind=db.get_bind()) as writer:
        state = writer.get(StripeCheckoutState, user_id)
        if state is None:
            state = StripeCheckoutState(user_id=user_id)
            writer.add(state)
        for key, value in values.items():
            setattr(state, key, value)
        writer.commit()


def _list_all(call, **params):
    rows = []
    while True:
        page = stripe_billing.as_dict(call(limit=100, **params))
        data = page.get("data", [])
        rows.extend(data)
        if not page.get("has_more"):
            return rows
        if not data or not data[-1].get("id"):
            raise HTTPException(503, "Stripe pagination is incomplete")
        params["starting_after"] = data[-1]["id"]


def _customer(db, user, state):
    if state and state.customer_id:
        return state.customer_id
    if not user.email:
        raise HTTPException(400, "User email is required for payment")
    # This one-time lookup also recovers customers created before the binding was
    # stored, even if their owner's e-mail has changed. Search is eventually consistent.
    customers = _list_all(stripe.Customer.list)
    own = [c for c in customers if (c.get("metadata") or {}).get("user_id") == str(user.id)]
    if len(own) > 1:
        raise HTTPException(409, "Multiple Stripe customers need reconciliation")
    customer = own[0] if own else stripe_billing.as_dict(stripe.Customer.create(
        email=user.email, name=user.full_name, metadata={"user_id": str(user.id)},
        idempotency_key=f"xtrix-customer-{user.id}"))
    _persist(db, user.id, customer_id=customer["id"])
    return customer["id"]


def _check_subscription(db, user_id, subscription_id):
    stripe_billing.lock_subscription(db, subscription_id)
    fresh = stripe_billing.as_dict(stripe.Subscription.retrieve(subscription_id))
    if fresh.get("status") not in ("canceled", "incomplete_expired"):
        owner = (fresh.get("metadata") or {}).get("user_id")
        if owner and owner != str(user_id):
            raise HTTPException(409, "Stripe subscription ownership needs reconciliation")
        stripe_billing.sync_subscription(db, fresh)
        db.commit()
        raise HTTPException(409, "Manage the existing Stripe subscription before starting another")


def _live_subscription(db, user_id, customer_id):
    for remote in _list_all(stripe.Subscription.list, customer=customer_id, status="all"):
        if remote.get("status") not in ("canceled", "incomplete_expired"):
            _check_subscription(db, user_id, remote["id"])


def create_session(db, user, plan_id, payload):
    stripe_billing.configured()
    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
               {"key": f"stripe-checkout:{user.id}"})
    existing = db.query(Subscription).filter(
        Subscription.user_id == user.id, Subscription.provider == "stripe",
        Subscription.status.notin_(["cancelled", "canceled", "expired", "incomplete_expired"]),
    ).first()
    if existing:
        raise HTTPException(409, "Manage the existing Stripe subscription before starting another")
    state = db.get(StripeCheckoutState, user.id)
    preferred_id = state.session_id if state else None
    customer_id = _customer(db, user, state)
    sessions = _list_all(stripe.checkout.Session.list, customer=customer_id)
    if state and state.session_id:
        saved = stripe_billing.as_dict(stripe.checkout.Session.retrieve(state.session_id))
        sessions = [s for s in sessions if s["id"] != saved["id"]] + [saved]
    if state and state.request_key and not state.session_id:
        recovered = next((s for s in sessions if (s.get("metadata") or {}).get("checkout_attempt") == state.request_key), None)
        if recovered is None:
            _live_subscription(db, user.id, customer_id)
            if not state.attempt_started_at or utcnow() - state.attempt_started_at >= timedelta(hours=23):
                # Stripe can prune idempotency keys after 24 hours. An unknown old
                # result needs reconciliation, never a blind second creation.
                raise HTTPException(409, "An older checkout attempt needs reconciliation")
            recovered = stripe_billing.as_dict(stripe.checkout.Session.create(
                **state.request_payload, idempotency_key=state.request_key))
            sessions.append(recovered)
        _persist(db, user.id, session_id=recovered["id"])
        preferred_id = recovered["id"]
    relevant = [s for s in sessions if s.get("mode") == "subscription"]
    opened = [s for s in relevant if s.get("status") == "open"]
    for session in opened:
        if (session.get("metadata") or {}).get("user_id") != str(user.id):
            raise HTTPException(409, "An open checkout has conflicting ownership")
    offer_code = (payload.get("metadata") or {}).get("offer_code")
    if offer_code:
        same = [s for s in opened if (s.get("metadata") or {}).get("offer_code") == str(offer_code)]
    else:
        same = [s for s in opened if (s.get("metadata") or {}).get("plan_id") == str(plan_id)]
    chosen = next((s for s in same if s["id"] == preferred_id), same[0] if same else None)
    # Expire every other payable session first. If a payment wins that race,
    # Stripe rejects expire and we stop; we must not create another subscription.
    for session in opened:
        if chosen is None or session["id"] != chosen["id"]:
            stripe.checkout.Session.expire(session["id"])
    _live_subscription(db, user.id, customer_id)
    for session in relevant:
        if session.get("status") == "complete":
            subscription_id = stripe_billing.object_id(session.get("subscription"))
            if not subscription_id:
                raise HTTPException(409, "Checkout payment is still being processed")
            _check_subscription(db, user.id, subscription_id)
    if chosen:
        _persist(db, user.id, session_id=chosen["id"])
        return {"session_id": chosen["id"], "url": chosen["url"]}
    key = "xtrix-checkout-" + str(uuid4())
    payload = {**payload, "customer": customer_id,
               "metadata": {**payload["metadata"], "checkout_attempt": key},
               "subscription_data": {**payload["subscription_data"], "metadata": {
                   **payload["subscription_data"]["metadata"], "checkout_attempt": key}}}
    _persist(db, user.id, request_key=key, request_payload=payload, session_id=None, attempt_started_at=utcnow())
    session = stripe_billing.as_dict(stripe.checkout.Session.create(**payload, idempotency_key=key))
    _persist(db, user.id, session_id=session["id"])
    return {"session_id": session["id"], "url": session["url"]}
