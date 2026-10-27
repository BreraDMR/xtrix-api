"""Explicit prices for an existing visit, without rounding or currency defaults."""
from decimal import Decimal, InvalidOperation
import re
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from app.schemas.limits import Currency

MAX_VISIT_PRICE = Decimal('99999999.99')
_MAX_CENTS = 9999999999


def _price(value) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, float, str)):
        raise ValueError('A numeric price is required')
    if isinstance(value, str) and re.fullmatch(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?', value) is None:
        raise ValueError('A decimal price is required')
    try:
        price = value if isinstance(value, Decimal) else Decimal(str(value))
    except InvalidOperation:
        raise ValueError('A decimal price is required') from None
    if not price.is_finite() or price < 0 or price > MAX_VISIT_PRICE:
        raise ValueError('Price must be finite and between 0 and 99999999.99')
    if price.as_tuple().exponent < -2:
        raise ValueError('Price must have at most two decimal places')
    return price


ExplicitPrice = Annotated[Decimal, BeforeValidator(_price)]


def _cents(price: Decimal) -> int:
    if price.is_zero():
        return 0
    sign, digits, exponent = price.as_tuple()
    return int(''.join(map(str, digits))) * 10 ** (exponent + 2)


def _total(prices) -> Decimal:
    cents = 0
    for price in prices:
        cents += _cents(price)
        if cents > _MAX_CENTS:
            raise ValueError('Total price exceeds 99999999.99')
    return Decimal(f'{cents // 100}.{cents % 100:02d}')


class _PriceInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')


class VisitItemPriceChange(_PriceInput):
    item_id: int = Field(gt=0, strict=True)
    price: ExplicitPrice


class VisitPriceChange(_PriceInput):
    currency: Currency
    expected_quote: str = Field(strict=True, pattern=r'^vprice1_[0-9a-f]{64}$')
    items: tuple[VisitItemPriceChange, ...] | None = None
    legacy_price: ExplicitPrice | None = None

    @model_validator(mode='after')
    def complete_shape(self):
        if (self.items is None) == (self.legacy_price is None):
            raise ValueError('Provide item prices or a legacy price, exclusively')
        if self.items is not None:
            if not self.items or len({item.item_id for item in self.items}) != len(self.items):
                raise ValueError('Provide nonempty item prices with unique IDs')
        self.total
        return self

    @property
    def total(self) -> Decimal:
        prices = (item.price for item in self.items) if self.items is not None else (self.legacy_price,)
        return _total(prices)
