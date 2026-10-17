from datetime import datetime, timedelta

from app.api.crud import crud_subscription
from app.schemas.subscription import SubscriptionCreate


def test_create_subscription_sets_period_end(db, make_user):
    user = make_user("specialist")
    # plans come from the 0022 migration seed
    plan = crud_subscription.list_plans(db)[0]

    sub = crud_subscription.create_subscription(
        db,
        SubscriptionCreate(
            user_id=user.id,
            plan_id=plan.id,
            provider="stripe",
            external_id="ext-1",
            status="active",
        ),
    )

    assert sub.current_period_end is not None
    expected = datetime.utcnow() + timedelta(days=plan.period_days)
    assert abs((sub.current_period_end - expected).total_seconds()) < 60


def test_only_admin_creates_subscriptions_directly(client, db, make_user, headers_for):
    plan = crud_subscription.list_plans(db)[0]
    spec = make_user("specialist")
    body = {"user_id": spec.id, "plan_id": plan.id, "provider": "apple", "status": "active"}
    assert client.post("/subscriptions/", json=body, headers=headers_for(spec)).status_code == 403
    assert client.post("/subscriptions/", json=body, headers=headers_for(make_user("admin"))).status_code == 200
