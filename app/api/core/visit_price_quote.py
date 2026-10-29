"""Pure stored-price projections and optimistic fingerprints, never credentials."""
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json

from app.schemas.limits import ISO_4217
from app.schemas.visit_price import VisitPriceChange

StoredMoney = Decimal | int | float | None
_NAMESPACE = 'xtrix.visit-price.quote.v1'


def _id(value, *, optional=False):
    if optional and value is None:
        return
    if type(value) is not int or value <= 0:
        raise ValueError('A positive stored ID is required')


def _stored_money(value):
    if value is not None and (isinstance(value, bool) or not isinstance(value, (Decimal, int, float))):
        raise ValueError('Stored money must be a scalar number or NULL')


@dataclass(frozen=True)
class StoredVisitPriceItem:
    id: int
    position: int
    service_id: int | None
    specialist_id: int
    price: StoredMoney

    def __post_init__(self):
        _id(self.id)
        _id(self.service_id, optional=True)
        _id(self.specialist_id)
        if type(self.position) is not int:
            raise ValueError('A stored integer position is required')
        _stored_money(self.price)


@dataclass(frozen=True)
class StoredVisitPrice:
    visit_id: int
    currency_snapshot: str | None
    total: StoredMoney
    service_id: int | None
    specialist_id: int
    items: tuple[StoredVisitPriceItem, ...] = ()

    def __post_init__(self):
        _id(self.visit_id)
        _id(self.service_id, optional=True)
        _id(self.specialist_id)
        _stored_money(self.total)
        if self.currency_snapshot is not None and not isinstance(self.currency_snapshot, str):
            raise ValueError('Stored currency must be a string or NULL')
        if type(self.items) is not tuple or not all(isinstance(item, StoredVisitPriceItem) for item in self.items):
            raise ValueError('Stored items must be an immutable scalar tuple')
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError('Stored item IDs must be unique')
        object.__setattr__(self, 'items', tuple(sorted(self.items, key=lambda item: (item.position, item.id))))


@dataclass(frozen=True)
class VisitPriceItemQuote:
    item_id: int
    position: int
    service_id: int | None
    specialist_id: int
    price: Decimal | None


@dataclass(frozen=True)
class VisitPriceQuote:
    visit_id: int
    currency: str | None
    total: Decimal | None
    items: tuple[VisitPriceItemQuote, ...]
    expected_quote: str


def _state(value):
    if not isinstance(value, StoredVisitPrice):
        raise ValueError('An explicit stored scalar snapshot is required')


def _decimal(value):
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _price_key(value):
    if value is None:
        return None
    number = _decimal(value)
    if not number.is_finite():
        return {'invalid': str(number)}
    if number.is_zero():
        return [0, '0', 0]
    sign, digits, exponent = number.as_tuple()
    digits = list(digits)
    # Decimal.normalize() uses the caller's context and can round a stored amount.
    while len(digits) > 1 and digits[-1] == 0:
        digits.pop()
        exponent += 1
    return [sign, ''.join(map(str, digits)), exponent]


def fingerprint(state: StoredVisitPrice) -> str:
    _state(state)
    payload = [_NAMESPACE, state.visit_id, state.currency_snapshot, _price_key(state.total),
               state.service_id, state.specialist_id,
               [[item.position, item.id, _price_key(item.price), item.service_id, item.specialist_id]
                for item in state.items]]
    encoded = json.dumps(payload, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode('ascii')
    return 'vprice1_' + hashlib.sha256(encoded).hexdigest()


def _display_price(value):
    if value is None:
        return None
    number = _decimal(value)
    return number if number.is_finite() else None


def project_price(state: StoredVisitPrice) -> VisitPriceQuote:
    _state(state)
    # Read stored finite values as-is, including old negatives; this is not a repair.
    return VisitPriceQuote(state.visit_id,
        state.currency_snapshot if state.currency_snapshot in ISO_4217 else None,
        _display_price(state.total), tuple(VisitPriceItemQuote(item.id, item.position, item.service_id,
            item.specialist_id, _display_price(item.price)) for item in state.items), fingerprint(state))


def validate_change_items(state: StoredVisitPrice, change: VisitPriceChange) -> Decimal:
    """Check all stored item IDs exactly once; auth/staleness/settlement belong to the caller."""
    _state(state)
    if not isinstance(change, VisitPriceChange):
        raise ValueError('An explicit validated price change is required')
    if state.items:
        if (change.items is None or len(change.items) != len(state.items)
                or {item.item_id for item in change.items} != {item.id for item in state.items}):
            raise ValueError('Price every stored item exactly once')
    elif change.legacy_price is None:
        raise ValueError('A visit without stored items requires a legacy price')
    return change.total
