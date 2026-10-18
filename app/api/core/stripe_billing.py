"""Stripe is the source of truth; webhook changes are committed together by the caller."""
from datetime import datetime, timezone

import stripe
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.core.clock import utcnow
from app.api.core.config import settings
from app.db.models.subscription import Subscription
from app.db.models.subscription_plan import SubscriptionPlan
from app.db.models.transaction import Transaction
from app.db.models.user import User


EVENTS = {
    "checkout.session.completed", "customer.subscription.created", "customer.subscription.updated",
    "customer.subscription.deleted", "invoice.payment_succeeded", "invoice.paid", "invoice.payment_failed",
}


def as_dict(value):
    # SDK 16 resources no longer implement dict.get(), including nested metadata.
    return value.to_dict() if isinstance(value, stripe.StripeObject) else value


def configured():
    if not settings.stripe_api_key:
        raise HTTPException(503, "Stripe not configured")
    stripe.api_key = settings.stripe_api_key


def lock_subscription(db: Session, subscription_id: str):
    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
               {"key": "stripe:" + subscription_id})


def object_id(value):
    value = as_dict(value)
    return value.get("id") if isinstance(value, dict) else value


def period_end(remote):
    # Since Basil the period belongs to each item, not the subscription itself.
    remote = as_dict(remote)
    ends = [item.get("current_period_end") for item in remote.get("items", {}).get("data", [])]
    ends = [end for end in ends if end is not None]
    if not ends:
        raise HTTPException(503, "Stripe subscription has no billing period")
    return datetime.fromtimestamp(min(ends), timezone.utc).replace(tzinfo=None)


def sync_subscription(db: Session, remote, metadata=None):
    remote = as_dict(remote)
    external_id = remote["id"]
    sub = db.query(Subscription).filter_by(provider="stripe", external_id=external_id).first()
    if sub is None:
        metadata = remote.get("metadata") or metadata or {}
        if not metadata.get("user_id") or not metadata.get("plan_id"):
            # Other subscriptions in the same Stripe account need not belong to Xtrix.
            return None
        try:
            user_id, plan_id = int(metadata["user_id"]), int(metadata["plan_id"])
        except (TypeError, ValueError):
            raise HTTPException(400, "Invalid subscription metadata")
        plan = db.get(SubscriptionPlan, plan_id)
        if not db.get(User, user_id) or not plan or plan.is_trial or plan.price <= 0:
            raise HTTPException(400, "Invalid subscription owner or paid plan")
        sub = Subscription(user_id=user_id, plan_id=plan_id, provider="stripe", external_id=external_id,
                           is_trial=False)
        db.add(sub)
    status = remote.get("status")
    if not status:
        raise HTTPException(503, "Stripe subscription has no status")
    sub.cancel_at_period_end = bool(remote.get("cancel_at_period_end", False))
    sub.status = "cancelled" if status == "canceled" else (
        "pending_cancellation" if status == "active" and sub.cancel_at_period_end else status)
    sub.current_period_end = period_end(remote)
    if status == "canceled" or sub.cancel_at_period_end:
        sub.cancelled_at = sub.cancelled_at or utcnow()
    else:
        sub.cancelled_at = None
    if status == "active" and sub.current_period_end > utcnow():
        # A paid plan replaces the signup trial. Keeping a longer trial active
        # would hide this subscription and could bring access back after past_due.
        db.query(Subscription).filter(
            Subscription.user_id == sub.user_id, Subscription.is_trial.is_(True),
            Subscription.status.in_(["active", "pending_cancellation"]),
        ).update({Subscription.status: "expired", Subscription.current_period_end: utcnow()},
                 synchronize_session="fetch")
    db.flush()
    return sub


def invoice_subscription(invoice):
    details = (invoice.get("parent") or {}).get("subscription_details") or {}
    return object_id(details.get("subscription") or invoice.get("subscription"))


def record_invoice(db: Session, sub: Subscription, invoice, *, succeeded: bool):
    invoice_id = invoice.get("id")
    if not invoice_id:
        raise HTTPException(400, "Stripe invoice has no id")
    transaction = db.query(Transaction).filter_by(provider="stripe", external_transaction_id=invoice_id).first()
    if transaction is not None and transaction.status == "succeeded":
        return
    if transaction is None:
        transaction = Transaction(subscription_id=sub.id, provider="stripe", external_transaction_id=invoice_id,
                                  payment_method="card")
        db.add(transaction)
    transaction.status = "succeeded" if succeeded else "failed"
    transaction.amount = invoice.get("amount_paid" if succeeded else "amount_due", 0) / 100
    transaction.currency = invoice.get("currency", "eur").upper()
    transaction.description = "Stripe invoice payment"
    db.flush()
    sub.last_transaction_id = transaction.id


def handle_event(db: Session, event):
    event = as_dict(event)
    kind = event.get("type")
    if kind not in EVENTS:
        return
    obj = event.get("data", {}).get("object", {})
    if kind == "checkout.session.completed":
        if obj.get("payment_status") != "paid":
            return
        subscription_id = object_id(obj.get("subscription"))
    elif kind.startswith("invoice."):
        subscription_id = invoice_subscription(obj)
    else:
        subscription_id = obj.get("id")
    if not subscription_id:
        return
    configured()
    # Serialize all delivery orders for one subscription, including its first creation.
    # No CRUD helper is used here: those commit before the whole event is complete.
    lock_subscription(db, subscription_id)
    remote = stripe.Subscription.retrieve(subscription_id)
    sub = sync_subscription(db, remote, obj.get("metadata"))
    if sub is not None and kind.startswith("invoice."):
        record_invoice(db, sub, obj, succeeded=kind != "invoice.payment_failed")


def cancel(db: Session, sub: Subscription, *, at_period_end: bool):
    configured()
    if not sub.external_id:
        raise HTTPException(400, "Stripe subscription id is missing")
    lock_subscription(db, sub.external_id)
    db.refresh(sub)
    remote = (stripe.Subscription.modify(sub.external_id, cancel_at_period_end=True) if at_period_end
              else stripe.Subscription.cancel(sub.external_id))
    sync_subscription(db, remote)
    db.commit()
    db.refresh(sub)
    return sub


def validate_reactivation(sub: Subscription, *, plan_id=None):
    if (sub.status != "pending_cancellation" or not sub.cancel_at_period_end or not sub.current_period_end
            or sub.current_period_end <= utcnow() or plan_id not in (None, sub.plan_id)):
        raise HTTPException(400, "Only a scheduled cancellation can be undone; use checkout for a new plan")


def reactivate(db: Session, sub: Subscription, *, plan_id=None):
    configured()
    if sub.external_id:
        lock_subscription(db, sub.external_id)
        db.refresh(sub)
    validate_reactivation(sub, plan_id=plan_id)
    if not sub.external_id:
        raise HTTPException(400, "Stripe subscription id is missing")
    remote = as_dict(stripe.Subscription.retrieve(sub.external_id))
    if (remote.get("status") != "active" or not remote.get("cancel_at_period_end")
            or period_end(remote) <= utcnow()):
        raise HTTPException(400, "Stripe subscription is no longer awaiting cancellation")
    remote = stripe.Subscription.modify(sub.external_id, cancel_at_period_end=False)
    sync_subscription(db, remote)
    sub.reactivated_at = utcnow()
    db.commit()
    db.refresh(sub)
    return sub
