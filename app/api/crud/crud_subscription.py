from datetime import timedelta
from typing import Optional
from sqlalchemy.orm import Session
from app.db.models.subscription import Subscription
from app.db.models.subscription_plan import SubscriptionPlan
from app.schemas.subscription import SubscriptionCreate, SubscriptionUpdate
from app.schemas.subscription_plan import SubscriptionPlanCreate, SubscriptionPlanUpdate
from app.api.core.clock import utcnow
def create_plan(db: Session, data: SubscriptionPlanCreate) -> SubscriptionPlan:
    plan = SubscriptionPlan(**data.model_dump())
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan
def update_plan(db: Session, plan: SubscriptionPlan, data: SubscriptionPlanUpdate) -> SubscriptionPlan:
    for k, v in data.model_dump(exclude_unset=True).items():
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
    period_end = data.current_period_end
    trial_start = data.trial_start
    trial_end = data.trial_end
    is_trial = data.is_trial if hasattr(data, 'is_trial') else False
    if not period_end:
        plan = get_plan(db, data.plan_id)
        if plan:
            if is_trial and trial_end:
                period_end = trial_end  
            else:
                period_end = utcnow() + timedelta(days=plan.period_days)
    payload = data.model_dump(exclude_unset=True) if hasattr(data, 'model_dump') else data.model_dump(exclude_unset=True)
    payload["current_period_end"] = period_end
    if "is_trial" not in payload:
        payload["is_trial"] = is_trial
    if "trial_start" not in payload:
        payload["trial_start"] = trial_start
    if "trial_end" not in payload:
        payload["trial_end"] = trial_end
    sub = Subscription(**payload)
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub
def create_trial_subscription(db: Session, user_id: int, role: str) -> Optional[Subscription]:
    from app.db.models.user import User
    from app.api.core.subscription_policy import calculate_trial_dates, get_crm_trial_days
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return None
    if user.employer_id or user.business_id:
        return None
    # Owners use the existing business trial; solo specialists keep their trial.
    if role == "business_owner":
        role = "admin"
    if role not in ("admin", "specialist"):
        return None

    trial_days = get_crm_trial_days()
    plan_code = f"trial_{role}_{trial_days}d"
    plan = get_plan_by_code(db, plan_code)
    if not plan:
        from app.schemas.subscription_plan import SubscriptionPlanCreate
        plan_data = SubscriptionPlanCreate(
            code=plan_code,
            name=f"Trial {role.title()} ({trial_days} Days)",
            period_days=trial_days,
            price=0.0,
            currency="czk",
            features=f"Trial CRM plan for {role} (D02 policy: {trial_days} days without credit card)",
            is_active=True,
            is_trial=True,
            trial_days=trial_days,
        )
        plan = create_plan(db, plan_data)

    trial_start, trial_end = calculate_trial_dates(trial_days)
    from app.schemas.subscription import SubscriptionCreate
    sub_data = SubscriptionCreate(
        user_id=user_id,
        plan_id=plan.id,
        status="active",
        current_period_end=trial_end,
        is_trial=True,
        trial_start=trial_start,
        trial_end=trial_end,
    )
    return create_subscription(db, sub_data)
def is_trial_active(db: Session, user_id: int) -> bool:
    sub = get_active_subscription(db, user_id)
    if not sub:
        return False
    if not sub.is_trial:
        return False
    if not sub.trial_end:
        return False
    return utcnow() < sub.trial_end
def subscription_payer_id(db: Session, user) -> Optional[int]:
    from app.api.core.permissions import get_staff_record
    from app.db.models.user import User
    staff = get_staff_record(db, user)
    owner_id = staff.business_owner_id if staff else user.employer_id
    if owner_id:
        owner = db.query(User).filter(
            User.id == owner_id,
            User.deleted_at.is_(None),
            User.role.in_(["business_owner", "admin"]),
        ).first()
        return owner.id if owner else None
    if user.role == "employee":
        return None
    return user.id

def check_subscription_access(db: Session, user_id: int, require_paid: bool = False) -> tuple[bool, Optional[str]]:
    from app.db.models.user import User
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return False, "User not found"
    payer_id = subscription_payer_id(db, user)
    if payer_id is None:
        return False, "No subscription owner"
    user_id = payer_id
    sub = get_active_subscription(db, user_id)
    if not sub:
        return False, "No active subscription"
    if sub.status not in ["active", "pending_cancellation"]:
        return False, f"Subscription status: {sub.status}"
    if sub.current_period_end and utcnow() > sub.current_period_end:
        return False, "Subscription expired"
    if require_paid:
        if sub.is_trial and not is_trial_active(db, user_id):
            return False, "Trial expired"
        elif sub.is_trial:
            return False, "Trial access not allowed for this feature"
    return True, None
def update_subscription(db: Session, sub: Subscription, data: SubscriptionUpdate) -> Subscription:
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(sub, k, v)
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub
def get_active_subscription(db: Session, user_id: int) -> Optional[Subscription]:
    return (
        db.query(Subscription)
        .filter(
            Subscription.user_id == user_id,
            Subscription.status.in_(["active", "pending_cancellation"])
        )
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
def get_subscription(db: Session, subscription_id: int) -> Optional[Subscription]:
    return db.query(Subscription).filter(Subscription.id == subscription_id).first()
def cancel_subscription(db: Session, subscription_id: int, cancel_at_period_end: bool = True, user_id: Optional[int] = None) -> Optional[Subscription]:
    sub = get_subscription(db, subscription_id)
    if not sub:
        return None
    if user_id and sub.user_id != user_id:
        return None
    if cancel_at_period_end:
        sub.cancel_at_period_end = True
        sub.cancelled_at = utcnow()
        sub.status = "pending_cancellation"  
    else:
        sub.cancel_at_period_end = False
        sub.cancelled_at = utcnow()
        sub.status = "cancelled"
        sub.current_period_end = utcnow()  
    db.commit()
    db.refresh(sub)
    try:
        from app.schemas.transaction import TransactionCreate
        from app.api.crud import crud_transaction
        transaction_data = TransactionCreate(
            subscription_id=sub.id,
            amount=0.0,
            currency="eur",
            payment_method="cancellation",
            status="cancelled",
            description=f"Subscription cancelled (at_period_end={cancel_at_period_end})",
            provider=sub.provider,
        )
        crud_transaction.create_transaction(db, transaction_data)
    except Exception as e:
        import logging
        logging.warning(f"Failed to log cancellation transaction: {e}")
    return sub
def reactivate_subscription(db: Session, subscription_id: int, plan_id: Optional[int] = None, user_id: Optional[int] = None) -> Optional[Subscription]:
    sub = get_subscription(db, subscription_id)
    if not sub:
        return None
    if user_id and sub.user_id != user_id:
        return None
    if sub.status not in ["cancelled", "pending_cancellation", "expired"]:
        return None
    if plan_id:
        plan = get_plan(db, plan_id)
        if plan:
            sub.plan_id = plan_id
            sub.current_period_end = utcnow() + timedelta(days=plan.period_days)
    sub.cancel_at_period_end = False
    sub.cancelled_at = None
    sub.reactivated_at = utcnow()
    sub.status = "active"
    if sub.current_period_end and sub.current_period_end < utcnow():
        plan = get_plan(db, sub.plan_id)
        if plan:
            sub.current_period_end = utcnow() + timedelta(days=plan.period_days)
    db.commit()
    db.refresh(sub)
    try:
        from app.schemas.transaction import TransactionCreate
        from app.api.crud import crud_transaction
        transaction_data = TransactionCreate(
            subscription_id=sub.id,
            amount=0.0,
            currency="eur",
            payment_method="reactivation",
            status="succeeded",
            description=f"Subscription reactivated (plan_id={plan_id or sub.plan_id})",
            provider=sub.provider,
        )
        crud_transaction.create_transaction(db, transaction_data)
    except Exception as e:
        import logging
        logging.warning(f"Failed to log reactivation transaction: {e}")
    return sub
def deactivate_expired_subscriptions(db: Session) -> int:
    now = utcnow()
    expired_subscriptions = (
        db.query(Subscription)
        .filter(
            Subscription.status == "pending_cancellation",
            Subscription.current_period_end <= now,
        )
        .all()
    )
    deactivated_count = 0
    for sub in expired_subscriptions:
        sub.status = "expired"
        sub.cancel_at_period_end = False
        db.commit()
        deactivated_count += 1
    expired_active = (
        db.query(Subscription)
        .filter(
            Subscription.status == "active",
            Subscription.current_period_end <= now,
            Subscription.cancel_at_period_end == False,
        )
        .all()
    )
    for sub in expired_active:
        if not sub.is_trial:  
            sub.status = "expired"
            db.commit()
            deactivated_count += 1
    return deactivated_count
