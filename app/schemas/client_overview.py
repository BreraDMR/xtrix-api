"""A client card's visit history, upcoming visit and money summary.

Prices and payment methods are only filled for callers allowed to see
financials; the history itself stays visible to the rest of the staff.
"""
from typing import List, Optional

from pydantic import BaseModel, Field

from app.schemas.datetimes import UtcDatetime


class ClientOverviewVisit(BaseModel):
    id: int
    date_start: UtcDatetime
    date_end: Optional[UtcDatetime] = None
    status: str
    service_name: Optional[str] = None
    specialist_name: Optional[str] = None
    price: Optional[float] = None
    payment_method: Optional[str] = None
    late_cancel: bool = False


class ClientOverviewStats(BaseModel):
    total_visits: int
    completed_visits: int
    cancelled_visits: int
    no_show_visits: int = 0
    late_cancel_visits: int = Field(0, description="Cancelled visits cancelled inside the salon's window")
    last_visit_at: Optional[UtcDatetime] = None
    next_visit_at: Optional[UtcDatetime] = None


class ClientOverviewPayments(BaseModel):
    currency: str
    total_paid: float
    paid_visits: int


class ClientAttendanceOut(BaseModel):
    """No-show counters and what they mean for booking this client."""
    no_show_count: int = 0
    late_cancel_count: int = 0
    online_booking_blocked: bool = Field(False, description="The client can't book online in this salon")
    rule_reached: bool = Field(False, description="The salon's no-show rule is reached right now")
    warning: Optional[str] = Field(None, description="Localized note for staff about to book this client")


class ClientOverviewOut(BaseModel):
    client_id: int
    stats: ClientOverviewStats
    next_visit: Optional[ClientOverviewVisit] = None
    upcoming_visits: List[ClientOverviewVisit]
    recent_visits: List[ClientOverviewVisit]
    payments: Optional[ClientOverviewPayments] = None
    attendance: ClientAttendanceOut = Field(default_factory=ClientAttendanceOut)
