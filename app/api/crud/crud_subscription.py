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
    trial_start = data.trial_start
    trial_end = data.trial_end
    is_trial = data.is_trial if hasattr(data, 'is_trial') else False
    
    if not period_end:
        plan = get_plan(db, data.plan_id)
        if plan:
            if is_trial and trial_end:
                period_end = trial_end  # Для trial используем trial_end
            else:
                period_end = datetime.utcnow() + timedelta(days=plan.period_days)
    
    payload = data.dict(exclude_unset=True) if hasattr(data, 'dict') else data.model_dump(exclude_unset=True)
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
    """
    Создать автоматическую trial подписку для нового пользователя.
    - Solo Specialist: 7-14 дней
    - Admin: 3-5 дней (демо)
    """
    from app.db.models.user import User
    
    # Проверяем, не является ли пользователь сотрудником (employee)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return None
    
    # Если пользователь уже является сотрудником (имеет employer_id), trial не создаем
    if user.employer_id:
        return None
    
    # Определяем количество дней trial в зависимости от роли
    if role == "admin":
        trial_days = 5  # Демо период для админа
    elif role == "specialist":
        trial_days = 14  # Trial для Solo Specialist
    else:
        return None  # Для других ролей trial не предусмотрен
    
    # Ищем или создаем trial план
    plan = get_plan_by_code(db, f"trial_{role}")
    if not plan:
        # Создаем trial план, если его нет
        from app.schemas.subscription_plan import SubscriptionPlanCreate
        plan_data = SubscriptionPlanCreate(
            code=f"trial_{role}",
            name=f"Trial {role.title()}",
            period_days=trial_days,
            price=0.0,
            currency="eur",
            features=f"Trial plan for {role}",
            is_active=True,
            is_trial=True,
            trial_days=trial_days,
        )
        plan = create_plan(db, plan_data)
    
    # Создаем trial подписку
    trial_start = datetime.utcnow()
    trial_end = trial_start + timedelta(days=trial_days)
    
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
    """Проверяет, активен ли trial период для пользователя."""
    sub = get_active_subscription(db, user_id)
    if not sub:
        return False
    
    if not sub.is_trial:
        return False
    
    if not sub.trial_end:
        return False
    
    return datetime.utcnow() < sub.trial_end


def check_subscription_access(db: Session, user_id: int, require_paid: bool = False) -> tuple[bool, Optional[str]]:
    """
    Проверяет доступ пользователя к платным функциям.
    Возвращает (has_access, reason).
    """
    from app.db.models.user import User
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return False, "User not found"
    
    # Сотрудники (employees) используют подписку админа
    if user.employer_id:
        # Проверяем подписку админа
        admin_sub = get_active_subscription(db, user.employer_id)
        if admin_sub and admin_sub.status == "active":
            # Если требуется платная подписка, проверяем что это не trial
            if require_paid:
                if admin_sub.is_trial and not is_trial_active(db, user.employer_id):
                    return False, "Admin trial expired"
                elif admin_sub.is_trial:
                    return False, "Trial access not allowed for this feature"
            return True, None
        return False, "Admin subscription not active"
    
    # Для админов и solo специалистов проверяем их собственную подписку
    sub = get_active_subscription(db, user_id)
    if not sub:
        return False, "No active subscription"
    
    if sub.status != "active":
        return False, f"Subscription status: {sub.status}"
    
    # Проверяем срок действия подписки
    if sub.current_period_end and datetime.utcnow() > sub.current_period_end:
        return False, "Subscription expired"
    
    # Если требуется платная подписка, проверяем что это не trial
    if require_paid:
        if sub.is_trial and not is_trial_active(db, user_id):
            return False, "Trial expired"
        elif sub.is_trial:
            return False, "Trial access not allowed for this feature"
    
    return True, None


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

