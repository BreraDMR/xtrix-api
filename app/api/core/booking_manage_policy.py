"""When a guest may still cancel their own booking.

The cutoff is one day before the visit. It is deliberately checked on the stored UTC
start instant rather than a calendar day, so it doesn't shift with DST.
"""
from datetime import datetime, timedelta
from typing import Optional, Protocol

from app.api.core.clock import utcnow

class BookingManageVisit(Protocol):
    """Only the stored facts used by the shared guest/visitor action rules."""

    @property
    def status(self) -> Optional[str]: ...

    @property
    def date_start(self) -> datetime: ...


CANCELLATION_CUTOFF = timedelta(hours=24)


class BookingActionNotAllowed(Exception):
    """A guest action is refused; the reason is a stable code the router localizes."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def cancellation_block_reason(visit: BookingManageVisit, now: Optional[datetime] = None) -> Optional[str]:
    if visit.status == "completed":
        return "booking_cancel_completed"
    if visit.status == "in_progress":
        return "booking_cancel_in_progress"
    if visit.status == "cancelled":
        return "booking_cancel_already"
    if visit.status == "awaiting_payment":
        # an unpaid hold can always be released; the deposit is not taken yet
        return None
    if visit.status != "planned":
        return "booking_cancel_unavailable"
    if visit.date_start - (now or utcnow()) < CANCELLATION_CUTOFF:
        return "booking_cancel_too_late"
    return None


def ensure_cancellation_allowed(visit: BookingManageVisit, now: Optional[datetime] = None) -> None:
    reason = cancellation_block_reason(visit, now)
    if reason:
        raise BookingActionNotAllowed(reason)


def confirmation_block_reason(visit: BookingManageVisit, now: Optional[datetime] = None) -> Optional[str]:
    if visit.status == "completed":
        return "booking_confirm_completed"
    if visit.status == "in_progress":
        return "booking_confirm_in_progress"
    if visit.status == "cancelled":
        return "booking_confirm_cancelled"
    if visit.status != "planned":
        return "booking_confirm_unavailable"
    if visit.date_start <= (now or utcnow()):
        return "booking_confirm_started"
    return None


def ensure_confirmation_allowed(visit: BookingManageVisit, now: Optional[datetime] = None) -> None:
    reason = confirmation_block_reason(visit, now)
    if reason:
        raise BookingActionNotAllowed(reason)


def reschedule_block_reason(visit: BookingManageVisit, now: Optional[datetime] = None) -> Optional[str]:
    if visit.status == "completed":
        return "booking_reschedule_completed"
    if visit.status == "in_progress":
        return "booking_reschedule_in_progress"
    if visit.status == "cancelled":
        return "booking_reschedule_cancelled"
    if visit.status != "planned":
        return "booking_reschedule_unavailable"
    current = now or utcnow()
    if visit.date_start <= current:
        return "booking_reschedule_started"
    # moving a late booking would otherwise bypass the cancellation cutoff
    if visit.date_start - current < CANCELLATION_CUTOFF:
        return "booking_reschedule_too_late"
    return None


def ensure_reschedule_allowed(visit: BookingManageVisit, now: Optional[datetime] = None) -> None:
    reason = reschedule_block_reason(visit, now)
    if reason:
        raise BookingActionNotAllowed(reason)
