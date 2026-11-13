"""Shared bits for "sum stored visit prices, but never across currencies".

Same rules as the financial report: a visit counts in the currency it was
saved with (currency_snapshot), no FX anywhere, visits with unknown currency
are counted but never summed, and one broken price makes its whole slice
"requires review" instead of quietly turning into zero.
"""
from decimal import Decimal, ROUND_HALF_UP, localcontext
from math import isfinite

from sqlalchemy import Numeric, String, and_, case, cast, func

from app.db.models.visit import Visit
from app.schemas.limits import ISO_4217

CENT = Decimal('0.01')


def quote_currency():
    """The stored currency of a visit, or NULL when it's unknown or junk."""
    return case((Visit.currency_snapshot.in_(sorted(ISO_4217)), Visit.currency_snapshot), else_=None)


def quote_cents(currency=None):
    """Stored price in integer cents, NULL when the price can't be trusted.

    The CASE keeps legacy NaN/infinity away from the cast, and going through
    text keeps the decimal value postgres reads (no double precision SUM).
    """
    currency = quote_currency() if currency is None else currency
    price = case((and_(Visit.price.is_not(None), Visit.price >= 0,
                       Visit.price < float('inf')), cast(cast(Visit.price, String()), Numeric())), else_=None)
    return case((and_(currency.is_not(None), price == func.trunc(price, 2)), price * 100), else_=None)


def money_columns(cents):
    return (func.count().label('count'), func.count(cents).label('valid_count'),
            func.sum(cents).label('total_cents'))


def from_cents(value) -> Decimal:
    integer = int(value)
    sign = '-' if integer < 0 else ''
    integer = abs(integer)
    return Decimal(f'{sign}{integer // 100}.{integer % 100:02d}')


def average(amount: Decimal | None, count: int) -> Decimal | None:
    if amount is None:
        return None
    if not count:
        return Decimal('0.00')
    with localcontext() as money_context:
        money_context.prec = max(32, len(amount.as_tuple().digits) + 16)
        return (amount / count).quantize(CENT, rounding=ROUND_HALF_UP)


def money_slice(currency, count, valid_count, total_cents) -> dict:
    """One currency bucket: exact amount, or None + requires_review."""
    count = int(count or 0)
    review = currency is None or int(valid_count or 0) != count
    amount = None if review else (from_cents(total_cents) if count else Decimal('0.00'))
    return {'currency': currency, 'amount': amount, 'average': average(amount, count),
            'count': count, 'requires_review': review}


def as_float(value):
    """Old float fields for the frontend; None stays None."""
    if value is None:
        return None
    number = float(value)
    return number if isfinite(number) else None


def currency_order(currency):
    # known currencies alphabetically, the unknown bucket last
    return (currency is None, currency or '')
