"""Public action hints only; no visit, identity, catalog or billing records."""
from typing import Literal

from pydantic import BaseModel, ConfigDict


BookingActionReason = Literal[
    "visitor_actions_unavailable",
    "booking_confirm_completed", "booking_confirm_in_progress", "booking_confirm_cancelled",
    "booking_confirm_unavailable", "booking_confirm_started",
    "booking_cancel_completed", "booking_cancel_in_progress", "booking_cancel_already",
    "booking_cancel_unavailable", "booking_cancel_too_late",
    "booking_reschedule_completed", "booking_reschedule_in_progress", "booking_reschedule_cancelled",
    "booking_reschedule_unavailable", "booking_reschedule_started", "booking_reschedule_too_late",
]


class VisitorBookingAction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    allowed: bool
    reason: BookingActionReason | None


class VisitorBookingActions(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    confirm: VisitorBookingAction
    cancel: VisitorBookingAction
    reschedule: VisitorBookingAction
