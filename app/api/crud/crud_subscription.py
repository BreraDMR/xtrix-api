from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.db.models.subscription import Subscription
from app.db.models.subscription_plan import SubscriptionPlan
from app.schemas.subscription import SubscriptionCreate, SubscriptionUpdate
from app.schemas.subscription_plan import SubscriptionPlanCreate, SubscriptionPlanUpdate


def create_plan(db: Session, data: SubscriptionPlanCreate) -> SubscriptionPlan:
    plan = SubscriptionPlan(**data.dict())
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


def update_plan(db: Session, plan: SubscriptionPlan, data: SubscriptionPlanUpdate) -> SubscriptionPlan:
    for k, v in data.dict(exclude_unset=True).items():
        setattr(plan, k, v)
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


def get_plan(db: Session, plan_id: int) -> Optional[SubscriptionPlan]:
    return db.query(SubscriptionPlan).filter(SubscriptionPlan.id == plan_id).first()


def get_plan_by_code(db: Session, code: str) -> Optional[SubscriptionPlan]:
    return db.query(SubscriptionPlan).filter(SubscriptionPlan.code == code).first()


def list_plans(db: Session, active_only: bool = True):
    q = db.query(SubscriptionPlan)
    if active_only:
        q = q.filter(SubscriptionPlan.is_active.is_(True))
    return q.all()


def create_subscription(db: Session, data: SubscriptionCreate) -> Subscription:
    # auto-set period end if not provided
    period_end = data.current_period_end
    if not period_end:
        plan = get_plan(db, data.plan_id)
        if plan:
            period_end = datetime.utcnow() + timedelta(days=plan.period_days)
    payload = data.dict()
    payload["current_period_end"] = period_end
    sub = Subscription(**payload)
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


def update_subscription(db: Session, sub: Subscription, data: SubscriptionUpdate) -> Subscription:
    for k, v in data.dict(exclude_unset=True).items():
        setattr(sub, k, v)
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


def get_active_subscription(db: Session, user_id: int) -> Optional[Subscription]:
    return (
        db.query(Subscription)
        .filter(Subscription.user_id == user_id, Subscription.status == "active")
        .order_by(Subscription.current_period_end.desc().nullslast())
        .first()
    )


def get_by_external(db: Session, provider: str, external_id: str) -> Optional[Subscription]:
    return (
        db.query(Subscription)
        .filter(
            Subscription.provider == provider,
            Subscription.external_id == external_id,
        )
        .first()
    )

