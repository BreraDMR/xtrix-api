from datetime import datetime, timezone
from typing import Optional, Tuple
from sqlalchemy import distinct, func
from sqlalchemy.orm import Session

from app.api.core.clock import utcnow
from app.db.models.usage_counter import UsageCounter
from app.db.models.visit import Visit

CRM_METERING_POLICY_VERSION = "2026-10-v1"


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
