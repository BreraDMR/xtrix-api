"""Explicit public history fields. CRM records and action eligibility do not belong here."""
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.datetimes import UtcDatetime
from app.schemas.limits import Currency


class _PublicHistoryModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')


class VisitorHistoryMoney(_PublicHistoryModel):
    amount: Decimal = Field(allow_inf_nan=False)
    currency: Currency


class VisitorHistoryBusiness(_PublicHistoryModel):
    name: str
    address: str | None
    phone: str | None
    email: str | None
    timezone: str | None


class VisitorHistoryItem(_PublicHistoryModel):
    id: int
    position: int
    service_name: str | None
    start: UtcDatetime
    end: UtcDatetime
    duration_minutes: int
    staff_name: str | None
    price: VisitorHistoryMoney | None


class VisitorHistoryVisit(_PublicHistoryModel):
    id: int
    business_id: int
    business: VisitorHistoryBusiness
    start: UtcDatetime
    end: UtcDatetime | None
    service_name: str | None
    status: str | None
    confirmed_at: UtcDatetime | None
    staff_name: str | None
    price: VisitorHistoryMoney | None
    items: tuple[VisitorHistoryItem, ...]


class VisitorHistoryCursor(_PublicHistoryModel):
    date_start: UtcDatetime
    id: int = Field(gt=0, strict=True)
    as_of: UtcDatetime | None = None


class VisitorHistoryPage(_PublicHistoryModel):
    entries: tuple[VisitorHistoryVisit, ...]
    next_cursor: VisitorHistoryCursor | None
