"""Guest deposit and salon payout contracts.

No card data, provider secret or internal merchant id leaves the server. Every money
field is computed from the stored attempt; the browser only picks a refund key.
"""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.base import InputModel
from app.schemas.datetimes import UtcDatetime
from app.schemas.errors import invalid
from app.schemas.limits import ShortText


class PublicPaymentOut(BaseModel):
    public_ref: str
    status: str
    amount: float
    remaining_amount: float
    refunded_amount: float
    currency: str
    checkout_url: Optional[str] = None
    hold_expires_at: UtcDatetime
    paid_at: Optional[UtcDatetime] = None


class PublicPaymentStatusOut(PublicPaymentOut):
    visit_status: str
    visit_start: UtcDatetime
    visit_end: UtcDatetime
    service_name: str
    timezone: str


class ServicePaymentRefundOut(BaseModel):
    id: int
    amount: float
    currency: str
    reason: Optional[str] = None
    status: str
    created_at: UtcDatetime


class ServicePaymentOut(BaseModel):
    id: int
    visit_id: int
    status: str
    provider: str
    client_name: str
    service_name: str
    visit_start: UtcDatetime
    visit_status: str
    currency: str
    amount: float
    remaining_amount: float
    refunded_amount: float
    refundable_amount: float
    hold_expires_at: UtcDatetime
    paid_at: Optional[UtcDatetime] = None
    created_at: UtcDatetime
    refunds: list[ServicePaymentRefundOut] = Field(default_factory=list)


class ServicePaymentRefundIn(InputModel):
    model_config = ConfigDict(extra="forbid")

    refund_key: str = Field(
        ...,
        min_length=8,
        max_length=80,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Caller-generated retry key: the same key never refunds twice",
    )
    amount: Optional[float] = Field(
        None,
        gt=0,
        le=1000000,
        description="Defaults to the whole unrefunded amount",
    )
    reason: Optional[ShortText] = None

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value):
        if value is None:
            return None
        value = value.strip()
        return value or None


class SandboxPaymentOutcomeIn(InputModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["paid", "failed"]
