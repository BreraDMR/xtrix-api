from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from app.api.core.clock import utcnow
from app.api.core.config import settings

CRM_TRIAL_DEFAULT_DAYS = 7


def get_crm_trial_days() -> int:
    """Return the single configured CRM trial duration in days (7 days)."""
    return getattr(settings, "crm_trial_duration_days", CRM_TRIAL_DEFAULT_DAYS)


def calculate_trial_dates(
    duration_days: Optional[int] = None,
    start_time: Optional[datetime] = None,
) -> Tuple[datetime, datetime]:
    """Calculate UTC trial start and end dates according to policy (7 days).

    Hours are calculated strictly in UTC for both owners and solo specialists.
    """
    days = duration_days if duration_days is not None else get_crm_trial_days()
    start = start_time or utcnow()
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    end = start + timedelta(days=days)
    return start, end
