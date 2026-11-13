"""Shared bits for "sum stored visit prices, but never across currencies".

Same rules as the financial report: a visit counts in the currency it was
saved with (currency_snapshot), no FX anywhere, visits with unknown currency
are counted but never summed, and one broken price makes its whole slice
"requires review" instead of quietly turning into zero.
"""
from decimal import Decimal, ROUND_HALF_UP, localcontext
from math import isfinite

from sqlalchemy import Numeric, String, and_, case, cast, func, select, tuple_

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


def priced_slice(currency, count, valid_count, total_cents) -> dict | None:
    """money_slice over visits that carry a price; None when there's no price at all.

    A planned visit without a price (and often without a currency yet) is not
    money, so it must not turn a clean currency into "requires review".
    """
    if not int(count or 0):
        return None
    return money_slice(currency, count, valid_count, total_cents)


def single_currency(slices) -> str | None:
    """The one known currency all these slices share, None for mixed/unknown/broken."""
    units = {item['currency'] for item in slices}
    if len(units) != 1 or any(item['requires_review'] for item in slices):
        return None
    return next(iter(units))


def grouped_quotes(db, keys, filters, joins=(), limit=None):
    """Per-row visit counts plus one money slice per stored currency, one statement.

    keys are labeled columns naming a row (say service id + name). Returns an
    ordered list of {'key': tuple, 'visits', 'clients', 'by_currency': [...]}.
    GROUPING SETS gives the per-row totals (distinct clients can't be added up
    over currencies) from the same snapshot as the per-currency slices.
    With limit, only the rows with the most visits are kept.
    """
    currency = quote_currency()
    # quote_* names so they never clash with a key like client_id
    query = select(*keys, Visit.client_id.label('quote_client'), Visit.price.label('quote_price'),
                   currency.label('quote_currency'), quote_cents(currency).label('quote_cents')).select_from(Visit)
    for target, on in joins:
        query = query.join(target, on)
    scoped = query.where(*filters).cte('scoped_quotes')
    key_columns = [scoped.c[key.key] for key in keys]
    grouped = select(
        *key_columns, scoped.c.quote_currency.label('currency'),
        func.grouping(scoped.c.quote_currency).label('is_total'),
        func.count().label('visits'), func.count(func.distinct(scoped.c.quote_client)).label('clients'),
        func.count(scoped.c.quote_price).label('count'), func.count(scoped.c.quote_cents).label('valid_count'),
        func.sum(scoped.c.quote_cents).label('total_cents'),
    ).group_by(func.grouping_sets(tuple_(*key_columns, scoped.c.quote_currency), tuple_(*key_columns)))
    if limit:
        top = select(*key_columns).group_by(*key_columns).order_by(
            func.count().desc(), *key_columns).limit(limit).subquery('top_rows')
        grouped = grouped.where(tuple_(*key_columns).in_(select(*top.c)))
    with db.no_autoflush:
        rows = db.execute(grouped).mappings().all()
    result = {}
    for row in rows:
        key = tuple(row[column.key] for column in key_columns)
        entry = result.setdefault(key, {'key': key, 'visits': 0, 'clients': 0, 'by_currency': []})
        if row['is_total']:
            entry['visits'], entry['clients'] = int(row['visits']), int(row['clients'])
            continue
        value = priced_slice(row['currency'], row['count'], row['valid_count'], row['total_cents'])
        if value is not None:
            value['client_count'] = int(row['clients'])
            entry['by_currency'].append(value)
    for entry in result.values():
        entry['by_currency'].sort(key=lambda item: currency_order(item['currency']))
    # most visits first, then by key (None keys last) so the order is stable
    return sorted(result.values(), key=lambda entry: (
        -entry['visits'], tuple((part is None, '' if part is None else part) for part in entry['key'])))


def per_client(value: dict) -> Decimal | None:
    """Money of one slice divided by its distinct clients."""
    return average(value['amount'], value['client_count'])
