"""AI billing must not hide the CRM trial or complete a different checkout."""
from datetime import timedelta
from types import SimpleNamespace

import pytest
import stripe

from app.api.core.clock import utcnow
from app.api.core.config import settings
from app.api.crud import crud_subscription
from app.db.models.subscription import Subscription


def _ai_subscription(db, owner, status="active"):
    plan = crud_subscription.get_plan_by_code(db, "solo_basic")
    sub = Subscription(user_id=owner.id, plan_id=plan.id, provider="stripe", external_id="sub_ai_paid",
                       is_trial=False, offer_code="ai_starter_czk", status=status,
                       current_period_end=utcnow() + timedelta(days=30))
    db.add(sub)
    db.commit()
    return sub


@pytest.mark.parametrize("expired", [False, True])
def test_crm_trial_status_ignores_an_active_ai_subscription(client, db, make_user, headers_for, expired):
    owner = make_user("business_owner")
    trial = db.query(Subscription).filter_by(user_id=owner.id, is_trial=True).one()
    if expired:
        trial.trial_end = utcnow() - timedelta(days=4)
        trial.current_period_end = trial.trial_end
        db.commit()
    _ai_subscription(db, owner)
    headers = headers_for(owner)
    response = client.get("/subscriptions/trial/status", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["is_trial"] is True
    assert data["is_trial_active"] is (not expired)
    assert data["has_access"] is (not expired)
    crm = client.get("/subscriptions/me?product=crm", headers=headers)
    if not expired:
        assert crm.json()["id"] == trial.id
    ai = client.get("/subscriptions/me?product=ai", headers=headers)
    assert ai.json()["external_id"] == "sub_ai_paid"


@pytest.mark.parametrize("paid_status", [None, "active", "past_due"])
def test_checkout_reports_readiness_of_the_exact_paid_subscription(client, db, make_user, headers_for, monkeypatch, paid_status):
    owner = make_user("business_owner")
    if paid_status:
        _ai_subscription(db, owner, paid_status)
    monkeypatch.setattr(settings, "stripe_api_key", "sk_test_local_mock_only")
    monkeypatch.setattr(stripe.checkout.Session, "retrieve", lambda _id: SimpleNamespace(
        id="cs_ai", metadata={"user_id": str(owner.id)}, payment_status="paid", subscription="sub_ai_paid"))
    response = client.get("/subscriptions/checkout/session/cs_ai", headers=headers_for(owner))
    assert response.status_code == 200, response.text
    assert response.json()["subscription_ready"] is (paid_status == "active")
