from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Tuple

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


def preserve_existing_subscription(
    existing_sub: Optional[Any],
    candidate_start: datetime,
    candidate_end: datetime,
) -> Tuple[datetime, datetime]:
    """Ensure existing paid periods or legacy trials (e.g. 14 days) are never shortened.

    Guarantees (D02 policy):
    1. Existing paid subscriptions (is_trial=False) are never overridden.
    2. Active legacy trial periods that exceed 7 days (e.g. 14 days) are preserved as-is.
    3. New registration or onboarding invocations do not shorten or reset existing periods.
    """
    if existing_sub is None:
        return candidate_start, candidate_end

    # If already paid, keep current period end
    if not getattr(existing_sub, "is_trial", False):
        period_end = getattr(existing_sub, "current_period_end", None)
        return getattr(existing_sub, "created_at", candidate_start), period_end or candidate_end

    # For existing trial, never shorten trial_end
    current_trial_end = getattr(existing_sub, "trial_end", None)
    if current_trial_end and current_trial_end > candidate_end:
        return getattr(existing_sub, "trial_start", candidate_start), current_trial_end

    return getattr(existing_sub, "trial_start", candidate_start), getattr(existing_sub, "trial_end", candidate_end)

