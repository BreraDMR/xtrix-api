"""Visitor-portal requests and responses.

Nothing here exposes staff notes, payment methods or another client's data. A portal
visit carries only what the visitor needs to recognise their own appointment.
"""
from typing import Any, Optional

from pydantic import BaseModel, EmailStr, Field

from app.schemas.base import InputModel
from app.schemas.datetimes import UtcDatetime


class ClientPortalLoginIn(InputModel):
    email: EmailStr


class ClientPortalMessageOut(BaseModel):
    message: str


class ClientPortalVerifyIn(InputModel):
    token: str = Field(..., min_length=20, max_length=128)


class ClientPortalSessionOut(BaseModel):
    session_token: str
    expires_at: UtcDatetime
    client_name: str
    business_name: str
    business_slug: Optional[str] = None
    language: str = "en"


class ClientPortalSessionInfoOut(BaseModel):
    expires_at: UtcDatetime
    client_name: str
    business_name: str
    business_slug: Optional[str] = None
    language: str = "en"


class ClientPortalVisitOut(BaseModel):
    id: int
    date_start: UtcDatetime
    date_end: Optional[UtcDatetime] = None
    service_id: Optional[int] = None
    service_name: Optional[str] = None
    staff_name: Optional[str] = None
    status: str
    price: Optional[float] = None
    can_manage: bool = False


class ClientPortalVisitsOut(BaseModel):
    next_offset: Optional[int] = None
    upcoming: list[ClientPortalVisitOut]
    past: list[ClientPortalVisitOut]
    booking_slug: Optional[str] = None
    booking_enabled: bool = False
    currency: str
    timezone: str


class ClientPortalLoyaltyOut(BaseModel):
    points: float
    cashback: float
    level_name: Optional[str] = None
    level_benefits: Optional[str] = None


class ClientPortalQuestionnaireOut(BaseModel):
    id: int
    visit_id: int
    name: str
    version: int
    submitted_at: UtcDatetime
    fields: list[dict[str, Any]]
    answers: dict[str, Any]


class ClientPortalQuestionnairesOut(BaseModel):
    items: list[ClientPortalQuestionnaireOut]


class ClientPortalManageLinkOut(BaseModel):
    token: str
    expires_at: UtcDatetime
