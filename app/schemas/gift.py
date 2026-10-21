"""Contracts for gift certificates and visit packages.

The buyer pays a fixed offer price; the certificate carries the face value or the
number of visits sold, never both. Every balance in an output is computed from the
ledger, so the browser cannot choose it.
"""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.schemas.base import InputModel
from app.schemas.datetimes import UtcDatetime
from app.schemas.errors import invalid
from app.schemas.limits import Name, PositiveMoney, ShortText


class GiftOfferIn(InputModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    description: Optional[str] = Field(None, max_length=500)
    kind: Literal["money", "package"]
    price: PositiveMoney
    face_value: Optional[PositiveMoney] = None
    visits_count: Optional[int] = Field(None, ge=1, le=100)
    validity_days: int = Field(..., ge=1, le=730)
    service_ids: list[int] = Field(default_factory=list, max_length=200)
    is_active: bool = True

    @field_validator("service_ids")
    @classmethod
    def unique_services(cls, value):
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def kind_units(self):
        if self.kind == "money":
            if self.face_value is None or self.face_value <= 0:
                raise invalid("gift_face_value_required", "A money certificate needs a face value")
            if self.visits_count is not None:
                raise invalid("gift_units_mixed", "A money certificate cannot carry visits")
        else:
            if self.visits_count is None:
                raise invalid("gift_visits_required", "A visit package needs a visit count")
            if self.face_value is not None:
                raise invalid("gift_units_mixed", "A visit package cannot carry a money value")
        return self


class GiftOfferOut(BaseModel):
    id: int
    business_id: int
    name: str
    description: Optional[str] = None
    kind: str
    price: float
    currency: str
    face_value: Optional[float] = None
    visits_count: Optional[int] = None
    validity_days: int
    service_ids: list[int] = Field(default_factory=list)
    is_active: bool
    created_at: UtcDatetime
    updated_at: UtcDatetime


class GiftShopOut(BaseModel):
    slug: str
    business_name: str
    currency: str
    payments_available: bool = False
    offers: list[GiftOfferOut] = Field(default_factory=list)


class GiftPurchaseIn(InputModel):
    model_config = ConfigDict(extra="forbid")

    offer_id: int = Field(..., gt=0)
    client_request_id: str = Field(
        ...,
        min_length=8,
        max_length=80,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Caller-generated retry key for one purchase",
    )
    buyer_name: Name
    buyer_email: EmailStr
    recipient_name: Optional[Name] = None
    recipient_email: Optional[EmailStr] = None
    message: Optional[str] = Field(None, max_length=500)
    processing_consent: bool

    @model_validator(mode="after")
    def consent_required(self):
        if self.processing_consent is not True:
            raise invalid("processing_consent_required", "Personal data processing consent is required")
        return self


class GiftCertificateOut(BaseModel):
    id: int
    status: str
    kind: str
    code: Optional[str] = None
    code_last4: str
    issuer_name: str
    offer_name: str
    currency: str
    face_value: float
    visits_total: int
    money_balance: float
    visits_remaining: int
    service_ids: list[int] = Field(default_factory=list)
    recipient_name: str
    recipient_email: Optional[str] = None
    buyer_name: str
    message: Optional[str] = None
    issued_at: UtcDatetime
    expires_at: UtcDatetime


class GiftOrderOut(BaseModel):
    public_ref: str
    status: str
    amount: float
    currency: str
    offer_name: str
    kind: str
    buyer_name: str
    recipient_name: str
    checkout_url: Optional[str] = None
    hold_expires_at: UtcDatetime
    paid_at: Optional[UtcDatetime] = None
    certificate: Optional[GiftCertificateOut] = None


class GiftLedgerEntryOut(BaseModel):
    id: int
    entry_type: str
    amount_delta: float
    units_delta: int
    request_key: str
    visit_id: Optional[int] = None
    redeems_entry_id: Optional[int] = None
    reason: Optional[str] = None
    created_at: UtcDatetime


class GiftCertificateDetailOut(GiftCertificateOut):
    order_id: int
    ledger: list[GiftLedgerEntryOut] = Field(default_factory=list)


class GiftRedemptionIn(InputModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., min_length=4, max_length=40)
    visit_id: int = Field(..., gt=0)
    amount: Optional[PositiveMoney] = None
    reason: Optional[ShortText] = None
    request_key: str = Field(
        ...,
        min_length=8,
        max_length=80,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Caller-generated retry key: the same key never redeems twice",
    )

    @field_validator("code")
    @classmethod
    def clean_code(cls, value):
        return value.strip()


class GiftRedemptionOut(BaseModel):
    certificate: GiftCertificateOut
    entry_id: int
    applied_amount: float
    applied_visits: int


class GiftReversalIn(InputModel):
    model_config = ConfigDict(extra="forbid")

    redemption_entry_id: int = Field(..., gt=0)
    request_key: str = Field(
        ...,
        min_length=8,
        max_length=80,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    reason: Optional[ShortText] = None


class GiftReversalOut(BaseModel):
    certificate: GiftCertificateOut
    entry_id: int
    restored_amount: float
    restored_visits: int


class GiftSandboxOutcomeIn(InputModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["paid", "failed"]
