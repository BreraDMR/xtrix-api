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


class ClientOverviewStats(BaseModel):
    total_visits: int
    completed_visits: int
    cancelled_visits: int
    last_visit_at: Optional[UtcDatetime] = None
    next_visit_at: Optional[UtcDatetime] = None


class ClientOverviewPayments(BaseModel):
    currency: str
    total_paid: float
    paid_visits: int


class ClientOverviewOut(BaseModel):
    client_id: int
    stats: ClientOverviewStats
    next_visit: Optional[ClientOverviewVisit] = None
    upcoming_visits: List[ClientOverviewVisit] = Field(default_factory=list)
    recent_visits: List[ClientOverviewVisit] = Field(default_factory=list)
    payments: Optional[ClientOverviewPayments] = None
