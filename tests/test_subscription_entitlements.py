"""Usage reports the limits of the offer that was actually bought, not a guessed tier."""
from datetime import timedelta

from app.api.core.clock import utcnow
from app.api.crud import crud_subscription
from app.db.models.subscription import Subscription
from app.schemas.subscription import SubscriptionCreate


def paid_subscription(db, user, offer_code: str | None):
    plan = crud_subscription.get_plan_by_code(db, "solo_basic")
    sub = crud_subscription.create_subscription(
        db,
        SubscriptionCreate(user_id=user.id, plan_id=plan.id, provider="stripe",
                           external_id=f"sub-{user.id}", status="active"),
    )
    sub.offer_code = offer_code
    db.commit()
    return sub


def usage(client, headers):
    return client.get("/subscriptions/usage", headers=headers)


def test_crm_offer_limits_come_from_the_purchased_offer(client, db, make_user, headers_for):
    owner = make_user("business_owner")
    paid_subscription(db, owner, "crm_starter_czk")
    body = usage(client, headers_for(owner)).json()
    assert body["crm"] == {"visits": 0, "active_clients": 0, "limit": 100,
                           "offer_code": "crm_starter_czk", "is_within_limit": True}
    # the AI product was not bought, so no AI starter limit is invented
    assert body["ai"] == {"daily_requests_used": 0, "daily_requests_limit": None,
                          "offer_code": None, "is_within_limit": True}


def test_ai_offer_limits_are_separate_from_crm(client, db, make_user, headers_for):
    owner = make_user("business_owner")
    paid_subscription(db, owner, "ai_pro_czk")
    body = usage(client, headers_for(owner)).json()
    assert body["ai"] == {"daily_requests_used": 0, "daily_requests_limit": 50,
                          "offer_code": "ai_pro_czk", "is_within_limit": True}
    assert body["crm"] == {"visits": 0, "active_clients": 0, "limit": None,
                           "offer_code": None, "is_within_limit": True}


def test_legacy_plan_does_not_borrow_a_new_catalog_tier(client, db, make_user, headers_for):
    owner = make_user("business_owner")
    paid_subscription(db, owner, None)
    body = usage(client, headers_for(owner)).json()
    assert body["crm"]["limit"] is None and body["crm"]["offer_code"] is None
    assert body["ai"]["daily_requests_limit"] is None and body["ai"]["offer_code"] is None


def test_expired_subscription_reports_no_entitlement(client, db, make_user, headers_for):
    owner = make_user("business_owner")
    paid_subscription(db, owner, "crm_starter_czk")
    # expire the signup trial too, so the paid offer cannot be shadowed by another active row
    for sub in db.query(Subscription).filter(Subscription.user_id == owner.id).all():
        sub.current_period_end = utcnow() - timedelta(seconds=1)
    db.commit()
    body = usage(client, headers_for(owner)).json()
    assert body["crm"]["offer_code"] is None and body["crm"]["limit"] is None
    assert body["ai"]["offer_code"] is None and body["ai"]["daily_requests_limit"] is None
