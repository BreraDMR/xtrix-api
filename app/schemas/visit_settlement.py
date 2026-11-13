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
