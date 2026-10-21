"""Usage accounting and the monthly limit (А3).

The allowance belongs to whoever pays: a business shares one pool between owner and staff, a solo
specialist has their own. The month is the calendar month in UTC.

Endpoints use reserve_request: count and add the row in one transaction under an advisory lock on
the payer, so two requests racing at the edge can't both get in. The row is filled in (settle) or
dropped (release, nothing was spent) once the model has answered.
"""
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session

from app.ai.config import AIConfig
from app.ai.errors import AILimitReached
from app.api.core.clock import utcnow
from app.api.core.config import settings
from app.api.core.permissions import get_user_business_id
from app.api.crud import crud_subscription
from app.db.models.ai_usage import AIUsage
from app.db.models.user import User


def _month_bounds(now: datetime) -> tuple[datetime, datetime]:
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return start, end


def plan_code(db: Session, user: User, now: Optional[datetime] = None) -> Optional[str]:
    """Code of the payer's live plan, or None when there's nothing paid or on trial right now."""
    payer_id = crud_subscription.subscription_payer_id(db, user)
    if payer_id is None:
        return None
    sub = crud_subscription.get_active_subscription(db, payer_id)
    if not sub or sub.status not in ("active", "pending_cancellation"):
        return None
    if sub.current_period_end and (now or utcnow()) > sub.current_period_end:
        return None
    return sub.plan.code if sub.plan else None


def _limit_for_plan(code: str, config: AIConfig) -> int:
    limit = config.monthly_limits.get(code)
    if limit is not None:
        return limit
    # trial codes carry their duration (trial_admin_7d); the role key is the allowance
    for role in ("trial_admin", "trial_specialist"):
        if code == role or code.startswith(role + "_"):
            return config.monthly_limits.get(role, config.monthly_limit_default)
    return config.monthly_limit_default


def monthly_limit(db: Session, user: User, config: AIConfig) -> int:
    code = plan_code(db, user)
    if code is None:
        return 0
    return _limit_for_plan(code, config)


# advisory lock namespaces, so a business id and a solo user id with the same number don't share a lock
_LOCK_BUSINESS = 0x41490001
_LOCK_SOLO = 0x41490002


def _scope(query, db: Session, user: User):
    business_id = get_user_business_id(user, db)
    if business_id:
        return query.filter(AIUsage.business_id == business_id)
    return query.filter(AIUsage.business_id.is_(None), AIUsage.user_id == user.id)


def used_this_month(db: Session, user: User, now: Optional[datetime] = None) -> int:
    start, end = _month_bounds(now or utcnow())
    q = db.query(func.count(AIUsage.id)).filter(AIUsage.created_at >= start, AIUsage.created_at < end)
    return _scope(q, db, user).scalar() or 0


def _day_bounds(now: datetime) -> tuple[datetime, datetime]:
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def _pilot_payer(db: Session, user: User) -> Optional[User]:
    payer_id = crud_subscription.subscription_payer_id(db, user)
    return db.get(User, payer_id) if payer_id is not None else None


def pilot_used_today(db: Session, user: User, now: Optional[datetime] = None) -> int:
    """Count successful or reserved chat requests in the payer's UTC day.

    A provider failure stays in the cost log but does not spend the customer's allowance.
    Speech is logged separately: transcribing and sending that text is one chat request.
    """
    payer = _pilot_payer(db, user)
    if payer is None:
        return 0
    start, end = _day_bounds(now or utcnow())
    query = db.query(func.count(AIUsage.id)).filter(
        AIUsage.created_at >= start,
        AIUsage.created_at < end,
        AIUsage.role != "stt",
        or_(AIUsage.succeeded.is_(True), AIUsage.provider == "pending"),
    )
    return int(_scope(query, db, payer).scalar() or 0)


def _pilot_limit(db: Session, user: User, now: datetime, *, speech: bool = False) -> int:
    # Imported here because subscription access itself uses the shared subscription CRUD.
    from app.api.core.subscription_access import get_subscription_access_state

    state = get_subscription_access_state(db, user.id)
    limit = state.get("ai_daily_limit") or 0
    used = pilot_used_today(db, user, now)
    if not state.get("can_use_ai") or (not speech and used >= limit):
        _, tomorrow = _day_bounds(now)
        raise AILimitReached(
            limit=limit if state.get("can_use_ai") else 0,
            used=used,
            retry_after=max(1, int((tomorrow - now).total_seconds())),
        )
    return max(0, limit - used)


def check_limit(db: Session, user: User, config: AIConfig, now: Optional[datetime] = None) -> int:
    """Requests left this month; raises AILimitReached when none are."""
    now = now or utcnow()
    if settings.pilot_mode_enabled:
        return _pilot_limit(db, user, now)
    limit = monthly_limit(db, user, config)
    used = used_this_month(db, user, now)
    if used >= limit:
        _, next_month = _month_bounds(now)
        raise AILimitReached(limit=limit, used=used, retry_after=max(1, int((next_month - now).total_seconds())))
    return limit - used


def record_usage(db: Session, user: User, *, provider: str, slot_index: Optional[int], model: str,
                 role: str, prompt_tokens: int, completion_tokens: int, cost_estimate: float,
                 succeeded: bool) -> AIUsage:
    row = AIUsage(
        business_id=get_user_business_id(user, db),
        user_id=user.id,
        provider=(provider or "unknown")[:64],
        slot_index=slot_index,
        model=(model or "unknown")[:128],
        role=role,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_estimate=cost_estimate,
        succeeded=succeeded,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def reserve_request(db: Session, user: User, config: AIConfig, *, role: str,
                    now: Optional[datetime] = None) -> AIUsage:
    """Reserve a request atomically before the model call. Pilot tiers use a daily limit."""
    now = now or utcnow()
    payer = _pilot_payer(db, user) if settings.pilot_mode_enabled else None
    scope_user = payer or user
    business_id = get_user_business_id(scope_user, db)
    space, key = (_LOCK_BUSINESS, business_id) if business_id else (_LOCK_SOLO, scope_user.id)
    # Held till commit; another request sees this pending row before calling its model.
    # Locking the scope also covers two owners belonging to the same salon.
    db.execute(text("SELECT pg_advisory_xact_lock(:space, :key)"), {"space": space, "key": key})
    try:
        if settings.pilot_mode_enabled:
            _pilot_limit(db, user, now, speech=role == "stt")
        else:
            check_limit(db, user, config, now)
    except AILimitReached:
        db.rollback()
        raise
    row = AIUsage(business_id=business_id, user_id=user.id, provider="pending", slot_index=None, model="-",
                  role=role, prompt_tokens=0, completion_tokens=0, cost_estimate=0.0, succeeded=False,
                  created_at=now)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def settle(db: Session, row: AIUsage, *, provider: str, slot_index: Optional[int], model: str,
           prompt_tokens: int, completion_tokens: int, cost_estimate: float, succeeded: bool) -> AIUsage:
    row.provider = (provider or "unknown")[:64]
    row.slot_index = slot_index
    row.model = (model or "unknown")[:128]
    row.prompt_tokens = prompt_tokens
    row.completion_tokens = completion_tokens
    row.cost_estimate = cost_estimate
    row.succeeded = succeeded
    db.commit()
    return row


def release(db: Session, row: AIUsage) -> None:
    """Give the request back - nothing reached a model, or nothing was spent."""
    db.rollback()
    db.query(AIUsage).filter(AIUsage.id == row.id).delete(synchronize_session=False)
    db.commit()
