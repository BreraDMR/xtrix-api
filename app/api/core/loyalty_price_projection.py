"""Read the sold quote and stored bonus deductions in one database snapshot."""
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select

from app.api.core import cashback_wallets as money
from app.db.models.loyalty_program import LoyaltyTransaction
from app.db.models.visit import Visit
from app.schemas.limits import ISO_4217


@dataclass(frozen=True)
class LoyaltyPrice:
    visit_id: int
    currency: str | None
    quoted_total: Decimal | None
    loyalty_discount: Decimal | None
    price_after_loyalty: Decimal | None
    requires_review: bool


def read_loyalty_price(db, authorized_visit):
    """Caller checked financial rights. This is a price, not a cash receipt or debt."""
    with db.no_autoflush:
        scoped = select(Visit.id, Visit.business_id, Visit.client_id, Visit.price, Visit.currency_snapshot).where(
            Visit.id == authorized_visit.id, Visit.business_id == authorized_visit.business_id,
            Visit.client_id == authorized_visit.client_id, Visit.specialist_id == authorized_visit.specialist_id,
            Visit.deleted_at.is_(None)).subquery()
        ledger = LoyaltyTransaction
        rows = db.execute(select(scoped, ledger.id.label('entry_id'),
            ledger.business_id.label('entry_business'), ledger.client_id.label('entry_client'),
            ledger.transaction_type, ledger.points_amount, ledger.cashback_amount, ledger.cashback_amount_exact,
            ledger.discount_currency, ledger.discount_amount_exact).select_from(scoped).outerjoin(
                ledger, ledger.visit_id == scoped.c.id).order_by(ledger.id)).mappings().all()
    if not rows:
        return None
    first = rows[0]
    currency = first['currency_snapshot'] if first['currency_snapshot'] in ISO_4217 else None
    try:
        gross = money._amount(first['price'])
        if gross < 0:
            raise money.CashbackError('visit_price_invalid')
        gross = money._from_cents(money._cents(gross))
    except money.CashbackError:
        gross = None
    unknown = currency is None or gross is None
    total_cents = 0
    for row in rows:
        if row['entry_id'] is None:
            continue
        if row['entry_business'] != first['business_id'] or row['entry_client'] != first['client_id']:
            unknown = True
            continue
        if row['transaction_type'] != 'spent':
            continue
        active = any(row[field] not in (None, 0) for field in
                     ('points_amount', 'cashback_amount', 'cashback_amount_exact', 'discount_amount_exact'))
        if not active:
            continue
        if currency is None or row['discount_currency'] != currency:
            unknown = True
            continue
        try:
            amount = money._amount(row['discount_amount_exact'])
            if amount <= 0:
                raise money.CashbackError('loyalty_payment_history_review')
        except money.CashbackError:
            unknown = True
            continue
        total_cents += money._cents(amount)
    if unknown:
        return LoyaltyPrice(first['id'], currency, gross, None, None, True)
    total = money._from_cents(total_cents)
    remaining_cents = money._cents(gross) - total_cents
    return LoyaltyPrice(first['id'], currency, gross, total,
                        money._from_cents(remaining_cents) if remaining_cents >= 0 else None,
                        remaining_cents < 0)
