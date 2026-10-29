"""Read-only booking action hints for an already authorized visitor target.

This does not establish session/grant authority or read billing/catalog state. The
caller supplies fresh scalar facts and one clock instant. Commands must recheck
these facts after their waits; a positive hint does not reserve a slot. Turning
off new public bookings deliberately is not an input for managing an old visit.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

from app.api.core import booking_manage_policy


@dataclass(frozen=True)
class BookingManageFacts:
    status: str | None
    start: datetime | None
    business_write_available: bool | None
    reschedule_catalog_available: bool | None


@dataclass(frozen=True)
class ActionEligibility:
    reason: str | None

    @property
    def allowed(self) -> bool:
        return self.reason is None


@dataclass(frozen=True)
class BookingManageEligibility:
    confirm: ActionEligibility
    cancel: ActionEligibility
    reschedule: ActionEligibility


@dataclass(frozen=True)
class _PolicyVisit:
    status: str | None
    date_start: datetime


def _utc(value: datetime) -> datetime:
    # DB timestamps are naive UTC; aware API facts represent the same instants.
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def booking_manage_eligibility(facts: BookingManageFacts, *, now: datetime) -> BookingManageEligibility:
    """Return immutable public reasons without database reads, writes or a clock read.

    None means an unknown fact, not a default permission. An unpaid hold with a
    known start keeps the legacy no-cutoff cancellation rule. Confirmation is
    still allowed when already confirmed: its command can return a no-op.
    Catalog availability only describes the current service/master allowlists,
    not a candidate slot. No subscription internals are included in the result.
    """
    if facts.business_write_available is not True:
        unavailable = ActionEligibility("visitor_actions_unavailable")
        return BookingManageEligibility(unavailable, unavailable, unavailable)
    if facts.start is None:
        return BookingManageEligibility(
            ActionEligibility("booking_confirm_unavailable"),
            ActionEligibility("booking_cancel_unavailable"),
            ActionEligibility("booking_reschedule_unavailable"),
        )
    visit = _PolicyVisit(status=facts.status, date_start=_utc(facts.start))
    current = _utc(now)
    confirm = booking_manage_policy.confirmation_block_reason(visit, current)
    cancel = booking_manage_policy.cancellation_block_reason(visit, current)
    reschedule = booking_manage_policy.reschedule_block_reason(visit, current)
    if reschedule is None and facts.reschedule_catalog_available is not True:
        reschedule = "booking_reschedule_unavailable"
    return BookingManageEligibility(
        ActionEligibility(confirm), ActionEligibility(cancel), ActionEligibility(reschedule),
    )
