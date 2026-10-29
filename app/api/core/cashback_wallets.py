"""Known currency cashback balances; legacy untyped money is read-only evidence."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.core.clock import utcnow
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.client_business_relation import ClientBusinessRelation
from app.db.models.client_cashback_balance import ClientCashbackBalance
from app.schemas.limits import ISO_4217

MAX_BALANCE = Decimal('9999999999999999.99')


class CashbackError(ValueError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class CashbackBalance:
    currency: str | None
    amount: Decimal | None
    requires_review: bool = False


@dataclass(frozen=True)
class CashbackMovement:
    currency: str
    amount: Decimal
    balance_after: Decimal


def _unit(currency):
    if not isinstance(currency, str) or currency not in ISO_4217:
        raise CashbackError('loyalty_cashback_currency_unavailable')
    return currency


def _amount(value):
    if isinstance(value, bool):
        raise CashbackError('loyalty_cashback_amount_invalid')
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise CashbackError('loyalty_cashback_amount_invalid') from None
    if not amount.is_finite() or amount.copy_abs() > MAX_BALANCE:
        raise CashbackError('loyalty_cashback_amount_invalid')
    _cents(amount)
    return amount


def _cents(amount):
    if amount.is_zero():
        return 0
    sign, digits, exponent = amount.as_tuple()
    digits = list(digits)
    while len(digits) > 1 and digits[-1] == 0:
        digits.pop()
        exponent += 1
    if exponent < -2:
        raise CashbackError('loyalty_cashback_amount_invalid')
    value = int(''.join(map(str, digits))) * 10 ** (exponent + 2)
    return -value if sign else value


def _from_cents(cents):
    sign = '-' if cents < 0 else ''
    value = abs(cents)
    return Decimal(f'{sign}{value // 100}.{value % 100:02d}')


def known_balance(db: Session, client_id: int, business_id: int, currency: str) -> Decimal:
    currency = _unit(currency)
    with db.no_autoflush:
        value = db.execute(select(ClientCashbackBalance.balance).where(
            ClientCashbackBalance.client_id == client_id, ClientCashbackBalance.business_id == business_id,
            ClientCashbackBalance.currency == currency)).scalar_one_or_none()
    return value if value is not None else Decimal('0.00')


def balances(db: Session, client_id: int, business_id: int) -> tuple[CashbackBalance, ...]:
    """Display each known wallet and the exact stored legacy value, without adding them."""
    with db.no_autoflush:
        rows = db.execute(select(ClientCashbackBalance.currency, ClientCashbackBalance.balance).where(
            ClientCashbackBalance.client_id == client_id, ClientCashbackBalance.business_id == business_id
        ).order_by(ClientCashbackBalance.currency)).all()
        legacy = db.execute(select(ClientBusinessRelation.loyalty_cashback).where(
            ClientBusinessRelation.client_id == client_id, ClientBusinessRelation.business_id == business_id)).scalar_one_or_none()
    result = [CashbackBalance(row.currency, row.balance) for row in rows]
    if legacy is not None and legacy != 0:
        amount = Decimal(str(legacy))
        result.append(CashbackBalance(None, amount if amount.is_finite() else None, True))
    return tuple(result)


def lock_money_references(db: Session, client_id: int, business_id: int) -> None:
    """Acquire business/client anchors before a visit or any customer balance."""
    with db.no_autoflush:
        if db.execute(select(Business.id).where(Business.id == business_id,
                Business.deleted_at.is_(None)).with_for_update(read=True)).scalar_one_or_none() is None:
            raise CashbackError('client_not_found')
        if db.execute(select(Client.id).where(Client.id == client_id,
                Client.deleted_at.is_(None)).with_for_update(read=True)).scalar_one_or_none() is None:
            raise CashbackError('client_not_found')


def move_balance(db: Session, client_id: int, business_id: int, currency: str, delta) -> CashbackMovement:
    """Stage a delta; caller writes the matching ledger and owns commit/whole rollback.

    All cooperating writers serialize on the client/business relation before the
    wallet. Its legacy float is never changed or converted by this helper.
    """
    currency, amount = _unit(currency), _amount(delta)
    with db.no_autoflush:
        if any(isinstance(row, ClientCashbackBalance) for row in db.new.union(db.dirty).union(db.deleted)):
            raise CashbackError('visit_price_retry')
        lock_money_references(db, client_id, business_id)
        relation = db.execute(select(ClientBusinessRelation.id).where(
            ClientBusinessRelation.client_id == client_id, ClientBusinessRelation.business_id == business_id
        ).with_for_update()).scalar_one_or_none()
        if relation is None:
            raise CashbackError('client_not_found')
        wallet = db.execute(select(ClientCashbackBalance.id, ClientCashbackBalance.balance).where(
            ClientCashbackBalance.client_id == client_id, ClientCashbackBalance.business_id == business_id,
            ClientCashbackBalance.currency == currency).with_for_update()).one_or_none()
        previous = wallet.balance if wallet is not None else Decimal('0.00')
        after_cents = _cents(previous) + _cents(amount)
        after = _from_cents(after_cents)
        if after < 0:
            raise CashbackError('loyalty_balance_insufficient')
        if not after.is_finite() or after > MAX_BALANCE:
            raise CashbackError('loyalty_cashback_amount_invalid')
        if amount.is_zero():
            return CashbackMovement(currency, _from_cents(_cents(amount)), previous)
        if wallet is None:
            row = ClientCashbackBalance(client_id=client_id, business_id=business_id,
                                        currency=currency, balance=after, updated_at=utcnow())
            db.add(row)
            db.flush([row])
        else:
            changed = db.execute(update(ClientCashbackBalance).where(ClientCashbackBalance.id == wallet.id).values(
                balance=after, updated_at=utcnow()).execution_options(synchronize_session=False))
            if changed.rowcount != 1:
                raise CashbackError('visit_price_retry')
        return CashbackMovement(currency, _from_cents(_cents(amount)), after)
