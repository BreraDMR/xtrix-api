from datetime import datetime, timezone
from typing import Optional, Tuple
from sqlalchemy import distinct, func
from sqlalchemy.orm import Session

from app.api.core.clock import utcnow
from app.db.models.usage_counter import UsageCounter
from app.db.models.visit import Visit

CRM_METERING_POLICY_VERSION = "2026-10-visits-v2"


def get_current_period(dt: Optional[datetime] = None) -> str:
    current = dt or utcnow()
    return current.strftime("%Y-%m")


def get_period_date_range(period: str) -> Tuple[datetime, datetime]:
    """Parse period string YYYY-MM into start and end UTC datetimes."""
    parts = period.split("-")
    year = int(parts[0])
    month = int(parts[1])
    start = datetime(year, month, 1, 0, 0, 0, tzinfo=timezone.utc)
    if month == 12:
        end = datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    else:
        end = datetime(year, month + 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    return start, end


def count_distinct_active_clients(
    db: Session,
    business_id: Optional[int],
    period: Optional[str] = None,
    user_id: Optional[int] = None,
) -> int:
    """Calculate the number of unique active clients for a business or solo user in the period.

    Policy rules (D01):
    1. Each unique client_id is counted at most once in the period.
    2. Cancelled visits (status == 'cancelled') are strictly excluded and do NOT increase the count.
    3. Soft-deleted and archived visits are excluded.
    4. Policy version is preserved.
    """
    period_str = period or get_current_period()
    start_dt, end_dt = get_period_date_range(period_str)

    query = db.query(func.count(distinct(Visit.client_id))).filter(
        Visit.deleted_at.is_(None),
        Visit.archived_at.is_(None),
        Visit.status != "cancelled",
        Visit.date_start >= start_dt,
        Visit.date_start < end_dt,
    )

    if business_id is not None:
        query = query.filter(Visit.business_id == business_id)
    elif user_id is not None:
        query = query.filter(Visit.specialist_id == user_id)
    else:
        return 0

    count = query.scalar() or 0
    return int(count)


def record_crm_usage(
    db: Session,
    business_id: Optional[int],
    period: Optional[str] = None,
    user_id: Optional[int] = None,
) -> UsageCounter:
    """Compute active clients and upsert into usage_counters table."""
    period_str = period or get_current_period()
    active_count = count_distinct_active_clients(
        db,
        business_id=business_id,
        period=period_str,
        user_id=user_id,
    )

    query = db.query(UsageCounter).filter(
        UsageCounter.product == "crm",
        UsageCounter.unit == "active_clients",
        UsageCounter.period == period_str,
    )
    if business_id is not None:
        query = query.filter(UsageCounter.business_id == business_id)
    elif user_id is not None:
        query = query.filter(UsageCounter.user_id == user_id)

    counter = query.first()
    if not counter:
        counter = UsageCounter(
            business_id=business_id,
            user_id=user_id,
            product="crm",
            period=period_str,
            unit="active_clients",
            count=active_count,
            policy_version=CRM_METERING_POLICY_VERSION,
        )
        db.add(counter)
    else:
        counter.count = active_count
        counter.policy_version = CRM_METERING_POLICY_VERSION
        counter.updated_at = utcnow()

    db.flush()
    return counter


def count_monthly_visits(db: Session, business_id: Optional[int], period: Optional[str] = None,
                         user_id: Optional[int] = None, exclude_id: Optional[int] = None) -> int:
    """Count visits, including repeated clients; cancelled and deleted rows don't count."""
    start, end = get_period_date_range(period or get_current_period())
    query = db.query(func.count(Visit.id)).filter(Visit.deleted_at.is_(None), Visit.status != "cancelled",
                                               Visit.date_start >= start, Visit.date_start < end)
    if business_id is not None:
        query = query.filter(Visit.business_id == business_id)
    elif user_id is not None:
        query = query.filter(Visit.business_id.is_(None), Visit.specialist_id == user_id)
    else:
        return 0
    if exclude_id is not None:
        query = query.filter(Visit.id != exclude_id)
    return int(query.scalar() or 0)


def enforce_visit_quota(db: Session, business_id: Optional[int], specialist_id: int, date_start: datetime,
                        status: str = "planned", exclude_id: Optional[int] = None) -> None:
    from fastapi import HTTPException
    from sqlalchemy import text
    from app.api.core.config import settings
    from app.api.core.offer_catalog import get_offer_by_code
    from app.api.core.subscription_access import TRIAL_VISIT_LIMIT, get_subscription_access_state, product_subscriptions
    from app.api.crud import crud_subscription
    from app.db.models.user import User

    if not settings.pilot_mode_enabled:
        return
    if business_id is not None:
        payer = db.query(User).filter(User.business_id == business_id, User.deleted_at.is_(None),
                                     User.role.in_(["business_owner", "admin"])).order_by(User.id).first()
    else:
        specialist = db.get(User, specialist_id)
        payer_id = crud_subscription.subscription_payer_id(db, specialist) if specialist else None
        payer = db.get(User, payer_id) if payer_id else None
    if payer is None or get_subscription_access_state(db, payer.id)["crm_mode"] != "full":
        raise HTTPException(status_code=402, detail={"code": "subscription_required"})
    if status == "cancelled":
        return
    # Lock the payer before counting; public, staff, and AI bookings share this lock.
    db.execute(text("SELECT pg_advisory_xact_lock(:space, :payer)"), {"space": 0x43524D01, "payer": payer.id})
    subscriptions = product_subscriptions(db, payer.id, "crm")
    sub = subscriptions[0] if subscriptions else None
    if sub is None:
        raise HTTPException(status_code=402, detail={"code": "subscription_required"})
    offer = get_offer_by_code(sub.offer_code) if sub.offer_code else None
    limit = TRIAL_VISIT_LIMIT if sub.is_trial else (offer.entitlements.max_monthly_visits if offer else None)
    if limit is None:
        return  # Existing paid legacy plans retain their original entitlement.
    used = count_monthly_visits(db, business_id, get_current_period(date_start),
                                payer.id if business_id is None else None, exclude_id)
    if used >= limit:
        raise HTTPException(status_code=402, detail={"code": "visit_limit_reached", "limit": limit})
