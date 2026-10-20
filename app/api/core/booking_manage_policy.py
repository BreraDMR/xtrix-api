"""When a guest may still cancel their own booking.

The cutoff is one day before the visit. It is deliberately checked on the stored UTC
start instant rather than a calendar day, so it doesn't shift with DST.
"""
from datetime import datetime, timedelta
from typing import Optional

from app.api.core.clock import utcnow
from app.db.models.visit import Visit

CANCELLATION_CUTOFF = timedelta(hours=24)


class BookingActionNotAllowed(Exception):
    """A guest action is refused; the reason is a stable code the router localizes."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def cancellation_block_reason(visit: Visit, now: Optional[datetime] = None) -> Optional[str]:
    if visit.status == "completed":
        return "booking_cancel_completed"
    if visit.status == "in_progress":
        return "booking_cancel_in_progress"
    if visit.status == "cancelled":
        return "booking_cancel_already"
    if visit.status != "planned":
        return "booking_cancel_unavailable"
    if visit.date_start - (now or utcnow()) < CANCELLATION_CUTOFF:
        return "booking_cancel_too_late"
    return None


def ensure_cancellation_allowed(visit: Visit, now: Optional[datetime] = None) -> None:
    reason = cancellation_block_reason(visit, now)
    if reason:
        raise BookingActionNotAllowed(reason)
