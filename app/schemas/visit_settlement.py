"""Explicit money entries on a visit: exact amount, the quote's own currency, no defaults."""
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from app.schemas.errors import invalid
from app.schemas.limits import Currency
from app.schemas.visit_price import _price


def _positive_amount(value) -> Decimal:
    amount = _price(value)
    if amount <= 0:
        # a zero entry records nothing; a negative one would hide a refund inside a receipt
        raise invalid('visit_settlement_amount_invalid', 'Amount must be above zero')
    return amount


ExactAmount = Annotated[Decimal, BeforeValidator(_positive_amount)]


class VisitSettlementEntryIn(BaseModel):
    """One charge, manual receipt or manual refund. Same key + same body = the saved entry back."""
    model_config = ConfigDict(frozen=True, extra='forbid')

    kind: Literal['charge', 'manual_receipt', 'manual_refund']
    amount: ExactAmount
    currency: Currency
    reason: str = Field(strict=True, min_length=3, max_length=500)
    reference: str | None = Field(default=None, strict=True, max_length=120,
                                  description='Receipt or document number, if there is one')
    idempotency_key: str = Field(strict=True, pattern=r'^[A-Za-z0-9_-]{8,80}$')

    @model_validator(mode='after')
    def readable_text(self):
        if len(self.reason.strip()) < 3:
            raise invalid('visit_settlement_reason_invalid', 'Explain the entry in a few words')
        return self


class VisitSettlementEntryOut(BaseModel):
    id: int
    visit_id: int
    kind: str
    amount: Decimal
    currency: str
    reason: str
    reference: str | None
    actor_user_id: int | None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class VisitSettlementEntryResultOut(BaseModel):
    entry: VisitSettlementEntryOut
    replayed: bool


class PriorCurrencySettlementOut(BaseModel):
    """Money left under a currency the visit price was corrected away from. Never converted."""
    currency: str
    provider_received: Decimal | None
    provider_refunded: Decimal | None
    provider_net: Decimal | None
    benefit_rows: int
    entry_rows: int
    model_config = ConfigDict(from_attributes=True)


class VisitSettlementOut(BaseModel):
    """What was paid and what is still owed, all in the visit's own saved currency."""
    visit_id: int
    currency: str | None
    gross_quote: Decimal | None
    loyalty_discount: Decimal | None
    price_after_loyalty: Decimal | None
    charges: Decimal | None
    gift_money_coverage: Decimal | None
    gift_units_coverage: int | None
    gift_package_coverage: Decimal | None
    provider_received: Decimal | None
    provider_refunded: Decimal | None
    provider_net: Decimal | None
    manual_received: Decimal | None
    manual_refunded: Decimal | None
    manual_payment_recorded: bool = Field(description='Old "paid manually" marker without an amount')
    has_pending_checkout: bool
    outstanding: Decimal | None = Field(description='Still to pay; null when any amount is unknown')
    overpayment: Decimal | None = Field(description='Paid above what is owed; null when any amount is unknown')
    outstanding_reason: str | None = Field(description='Why outstanding is null, a stable code')
    requires_review: bool
    reasons: tuple[str, ...]
    prior_currency_settlements: tuple[PriorCurrencySettlementOut, ...]
    message: str | None = None
    model_config = ConfigDict(from_attributes=True)
