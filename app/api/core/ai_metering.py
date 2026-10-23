from datetime import datetime
from typing import Optional
from uuid import UUID
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.api.core.clock import utcnow
from app.db.models.usage_counter import UsageCounter

AI_METERING_POLICY_VERSION = "2026-10-v1"


def get_current_daily_period(dt: Optional[datetime] = None) -> str:
    """Return period formatted as YYYY-MM-DD for daily AI quota tracking."""
    current = dt or utcnow()
    return current.strftime("%Y-%m-%d")


def get_daily_ai_usage(
    db: Session,
    business_id: Optional[int],
    user_id: Optional[int] = None,
    day_str: Optional[str] = None,
) -> int:
    """Get total AI units consumed for a specific day.

    Only successfully generated assistant responses count.
    Technical errors, replayed requests, and plan execution do not consume units.
    """
    period_str = day_str or get_current_daily_period()
    query = db.query(func.coalesce(func.sum(UsageCounter.count), 0)).filter(
        UsageCounter.product == "ai",
        UsageCounter.unit == "requests",
        UsageCounter.period == period_str,
    )
    if business_id is not None:
        query = query.filter(UsageCounter.business_id == business_id)
    elif user_id is not None:
        query = query.filter(UsageCounter.business_id.is_(None), UsageCounter.user_id == user_id)
    else:
        return 0

    return int(query.scalar())


def can_consume_ai_unit(
    db: Session,
    business_id: Optional[int],
    daily_limit: int,
    user_id: Optional[int] = None,
    day_str: Optional[str] = None,
) -> bool:
    """Check if the business or solo specialist is within the daily quota limit."""
    used = get_daily_ai_usage(
        db,
        business_id=business_id,
        user_id=user_id,
        day_str=day_str,
    )
    return used < daily_limit


def consume_ai_unit(
    db: Session,
    business_id: Optional[int],
    request_id: UUID,
    succeeded: bool,
    is_execution: bool = False,
    is_replay: bool = False,
    user_id: Optional[int] = None,
    day_str: Optional[str] = None,
) -> Optional[UsageCounter]:
    """Record consumption of an AI unit with strict separation of errors and execution.

    Policy rules (D01 / PRICE-06):
    1. If is_execution is True: confirm/execute does NOT deduct an AI unit.
    2. If is_replay is True: idempotent replay does NOT deduct an AI unit.
    3. If succeeded is False: technical failure or timeout does NOT deduct an AI unit.
    4. Only a fresh, successful generation increments the daily request count.
    """
    if is_execution or is_replay or not succeeded:
        return None

    period_str = day_str or get_current_daily_period()

    if business_id is None and user_id is None:
        raise ValueError("An assistant usage counter needs a business or solo user")
    scope = f"business:{business_id}" if business_id is not None else f"user:{user_id}"
    # The lock also covers the first insert, where there is no counter row to lock.
    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
               {"key": f"assistant-meter:{scope}:{period_str}"})

    query = db.query(UsageCounter).filter(
        UsageCounter.product == "ai",
        UsageCounter.unit == "requests",
        UsageCounter.period == period_str,
    )
    if business_id is not None:
        query = query.filter(UsageCounter.business_id == business_id)
    elif user_id is not None:
        query = query.filter(UsageCounter.business_id.is_(None), UsageCounter.user_id == user_id)

    counter = query.populate_existing().order_by(UsageCounter.id).with_for_update().first()
    if not counter:
        counter = UsageCounter(
            business_id=business_id,
            user_id=user_id,
            product="ai",
            period=period_str,
            unit="requests",
            count=1,
            policy_version=AI_METERING_POLICY_VERSION,
        )
        db.add(counter)
    else:
        counter.count += 1
        counter.policy_version = AI_METERING_POLICY_VERSION
        counter.updated_at = utcnow()

    db.flush()
    return counter
