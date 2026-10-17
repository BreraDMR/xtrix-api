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
