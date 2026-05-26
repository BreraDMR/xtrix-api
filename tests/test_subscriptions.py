from datetime import datetime, timedelta
from app.api.crud import crud_subscription
from app.db.models.subscription import Subscription
from app.db.models.subscription_plan import SubscriptionPlan
class DummySession:
    def __init__(self, plan=None):
        self.added = []
        self.committed = False
        self.plan = plan
    def add(self, obj):
        self.added.append(obj)
    def commit(self):
        self.committed = True
    def refresh(self, obj):
        pass
    def query(self, model):
        class Q:
            def __init__(self, model, outer):
                self.model = model
                self.outer = outer
            def filter(self, *args, **kwargs):
                return self
            def order_by(self, *args, **kwargs):
                return self
            def first(self):
                if self.model.__name__ == "SubscriptionPlan":
                    return self.outer.plan
                return None
        return Q(model, self)
def test_create_subscription_sets_period_end():
    plan = SubscriptionPlan(id=1, code="basic", name="Basic", period_days=30, price=10, currency="usd", is_active=True)  # type: ignore
    db = DummySession(plan=plan)
    sub = crud_subscription.create_subscription(
        db,
        data=type(
            "Obj",
            (),
            {
                "user_id": 1,
                "plan_id": plan.id,
                "provider": "stripe",
                "external_id": "ext-1",
                "status": "active",
                "current_period_end": None,
                "dict": lambda self=None: {
                    "user_id": 1,
                    "plan_id": plan.id,
                    "provider": "stripe",
                    "external_id": "ext-1",
                    "status": "active",
                    "current_period_end": None,
                },
            },
        )(),
    )
    assert sub.current_period_end is not None
