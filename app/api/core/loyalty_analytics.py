"""Loyalty points and currency-scoped cashback reports, without conversion."""
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from typing import Dict, List, Any, Optional

from sqlalchemy.orm import Session

from app.db.models.loyalty_program import LoyaltyTransaction
from app.db.models.client_business_relation import ClientBusinessRelation
from app.db.models.client_cashback_balance import ClientCashbackBalance
from app.api.crud import crud_loyalty


def _decimal(value):
    if value is None:
        return None
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _exact_add(left, right):
    # Reserve every whole/fractional digit plus a possible carry. A caller's
    # small Decimal context must never round an already stored monetary amount.
    whole_digits = max(max(0, len(value.as_tuple().digits) + value.as_tuple().exponent)
                       for value in (left, right))
    fractional_digits = max(max(0, -value.as_tuple().exponent) for value in (left, right))
    with localcontext() as context:
        context.prec = max(1, whole_digits + fractional_digits + 1)
        return left + right


def _bucket(buckets, currency):
    if currency not in buckets:
        # Unknown entries may themselves represent different old currencies.
        initial = None if currency is None else Decimal('0')
        buckets[currency] = {'earned': initial, 'spent': initial, 'balance': initial}
    return buckets[currency]


def _add(buckets, currency, field, amount):
    bucket = _bucket(buckets, currency)
    if currency is None or amount is None or bucket[field] is None:
        bucket[field] = None
    else:
        bucket[field] = _exact_add(bucket[field], amount)


def _flows(transactions):
    buckets = {}
    for transaction in transactions:
        currency = transaction.cashback_currency
        value = (transaction.cashback_amount_exact if currency is not None
                 else transaction.cashback_amount)
        amount = _decimal(value)
        if amount is None:
            if value is not None or currency is not None:
                _bucket(buckets, currency)
                if currency is not None:
                    _add(buckets, currency, 'earned', None)
                    _add(buckets, currency, 'spent', None)
            continue
        if transaction.transaction_type == 'earned' and amount > 0:
            _add(buckets, currency, 'earned', amount)
        elif transaction.transaction_type == 'spent' and amount < 0:
            _add(buckets, currency, 'spent', amount.copy_abs())
    return buckets


def _ordered(buckets):
    return sorted(buckets, key=lambda currency: (currency is None, currency or ''))


def _scalar(buckets, field):
    if not buckets:
        return Decimal('0'), None
    if len(buckets) == 1 and None not in buckets:
        currency = next(iter(buckets))
        return buckets[currency][field], currency
    return None, None


def _rate(earned, spent):
    if earned is None or spent is None:
        return None
    if earned <= 0:
        return 0.0
    with localcontext() as context:
        context.prec = max(32, len(earned.as_tuple().digits) + len(spent.as_tuple().digits) + 16)
        return float(spent / earned * 100)


def _transactions(db, business_id, date_from=None, date_to=None):
    query = db.query(LoyaltyTransaction).filter(LoyaltyTransaction.business_id == business_id)
    if date_from:
        query = query.filter(LoyaltyTransaction.created_at >= date_from)
    if date_to:
        query = query.filter(LoyaltyTransaction.created_at <= date_to)
    return query.order_by(LoyaltyTransaction.created_at, LoyaltyTransaction.id).all()


def get_loyalty_statistics(db: Session, business_id: int,
                          date_from: Optional[datetime] = None,
                          date_to: Optional[datetime] = None) -> Dict[str, Any]:
    # A removed program does not remove its ledger or the client's wallets.
    transactions = _transactions(db, business_id, date_from, date_to)
    total_points_earned = sum(t.points_amount for t in transactions
                             if t.transaction_type == 'earned' and t.points_amount > 0)
    total_points_spent = abs(sum(t.points_amount for t in transactions
                                if t.transaction_type == 'spent' and t.points_amount < 0))
    relations = db.query(ClientBusinessRelation).filter(
        ClientBusinessRelation.business_id == business_id).all()
    buckets = _flows(transactions)
    wallets = db.query(ClientCashbackBalance).filter(
        ClientCashbackBalance.business_id == business_id).all()
    for wallet in wallets:
        _add(buckets, wallet.currency, 'balance', _decimal(wallet.balance))
    if any(row.loyalty_cashback != 0 for row in relations):
        _bucket(buckets, None)
    earned, currency = _scalar(buckets, 'earned')
    spent, _ = _scalar(buckets, 'spent')
    balance, _ = _scalar(buckets, 'balance')
    by_currency = [{'currency': unit, **buckets[unit],
                    'redemption_rate': _rate(buckets[unit]['earned'], buckets[unit]['spent'])}
                   for unit in _ordered(buckets)]
    return {
        'total_participants': len({t.client_id for t in transactions}),
        'total_points_earned': total_points_earned,
        'total_points_spent': total_points_spent,
        'total_cashback_earned': earned,
        'total_cashback_spent': spent,
        'active_points_balance': sum(row.loyalty_points or 0.0 for row in relations),
        'active_cashback_balance': balance,
        'points_redemption_rate': (total_points_spent / total_points_earned * 100) if total_points_earned > 0 else 0.0,
        'cashback_redemption_rate': _rate(earned, spent),
        'cashback_currency': currency,
        'cashback_by_currency': by_currency,
    }


def get_level_distribution(
    db: Session,
    business_id: int,
) -> List[Dict[str, Any]]:
    program = crud_loyalty.get_loyalty_program(db, business_id)
    if not program or not program.use_levels:
        return []
    levels = crud_loyalty.list_loyalty_levels(db, program_id=program.id)
    if not levels:
        return []
    distribution = []
    for level in levels:
        count = db.query(ClientBusinessRelation).filter(
            ClientBusinessRelation.business_id == business_id,
            ClientBusinessRelation.loyalty_level_id == level.id,
        ).count()
        distribution.append({
            "level_id": level.id,
            "level_name": level.name,
            "level_number": level.level_number,
            "client_count": count,
            "min_spending": level.min_spending,
            "color": level.color,
        })
    no_level_count = db.query(ClientBusinessRelation).filter(
        ClientBusinessRelation.business_id == business_id,
        ClientBusinessRelation.loyalty_level_id.is_(None),
    ).count()
    if no_level_count > 0:
        distribution.append({
            "level_id": None,
            "level_name": "No Level",
            "level_number": 0,
            "client_count": no_level_count,
            "min_spending": 0.0,
            "color": None,
        })
    return sorted(distribution, key=lambda x: x["level_number"])

def get_top_clients_by_points(db: Session, business_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    from app.db.models.client import Client

    relations = {row.client_id: row for row in db.query(ClientBusinessRelation).filter(
        ClientBusinessRelation.business_id == business_id).all()}
    wallets = {}
    for wallet in db.query(ClientCashbackBalance).filter(ClientCashbackBalance.business_id == business_id).all():
        wallets.setdefault(wallet.client_id, []).append(wallet)
    candidates = []
    for client_id in relations.keys() | wallets.keys():
        relation = relations.get(client_id)
        points = relation.loyalty_points if relation else 0.0
        legacy = relation.loyalty_cashback if relation else 0.0
        balances = [{'currency': wallet.currency, 'amount': _decimal(wallet.balance),
                     'requires_review': _decimal(wallet.balance) is None}
                    for wallet in sorted(wallets.get(client_id, []), key=lambda row: row.currency)]
        if legacy != 0:
            balances.append({'currency': None, 'amount': _decimal(legacy), 'requires_review': True})
        if (points or 0.0) <= 0 and not any(row['amount'] is None or row['amount'] > 0 for row in balances):
            continue
        bucket = {row['currency']: {'balance': row['amount']} for row in balances}
        cashback, currency = _scalar(bucket, 'balance')
        candidates.append({'client_id': client_id, 'points': points or 0.0,
            'cashback': cashback, 'cashback_currency': currency, 'cashback_balances': balances,
            'level_id': relation.loyalty_level_id if relation else None})
    clients = {row.id: row.full_name for row in db.query(Client).filter(
        Client.id.in_([row['client_id'] for row in candidates])).all()}
    candidates = [dict(row, client_name=clients[row['client_id']]) for row in candidates if row['client_id'] in clients]
    return sorted(candidates, key=lambda row: (-row['points'], row['client_id']))[:limit]


def get_promotion_effectiveness(db: Session, business_id: int,
                                date_from: Optional[datetime] = None,
                                date_to: Optional[datetime] = None) -> List[Dict[str, Any]]:
    from app.db.models.loyalty_program import LoyaltyPromotion

    program = crud_loyalty.get_loyalty_program(db, business_id)
    if not program:
        return []
    query = db.query(LoyaltyPromotion).filter(LoyaltyPromotion.program_id == program.id)
    if date_from:
        query = query.filter(LoyaltyPromotion.start_date >= (date_from.date() if isinstance(date_from, datetime) else date_from))
    if date_to:
        query = query.filter(LoyaltyPromotion.end_date <= (date_to.date() if isinstance(date_to, datetime) else date_to))
    promotions = query.all()
    transactions_by_promotion = {}
    if promotions:
        for transaction in _transactions(db, business_id, date_from, date_to):
            transactions_by_promotion.setdefault(transaction.promotion_id, []).append(transaction)
    result = []
    for promotion in promotions:
        transactions = transactions_by_promotion.get(promotion.id, [])
        # Promotion totals include every positive award, as before, but keep units.
        buckets = {}
        for transaction in transactions:
            currency = transaction.cashback_currency
            value = (transaction.cashback_amount_exact if currency is not None else transaction.cashback_amount)
            amount = _decimal(value)
            if amount is not None and amount > 0:
                _add(buckets, currency, 'earned', amount)
            elif currency is not None and amount is None:
                _add(buckets, currency, 'earned', None)
            elif value is not None and amount is None:
                _bucket(buckets, None)
        amount, currency = _scalar(buckets, 'earned')
        result.append({
            'promotion_id': promotion.id, 'promotion_name': promotion.name,
            'promotion_type': promotion.promotion_type,
            'start_date': promotion.start_date.isoformat() if isinstance(promotion.start_date, date) else promotion.start_date,
            'end_date': promotion.end_date.isoformat() if isinstance(promotion.end_date, date) else promotion.end_date,
            'is_active': promotion.is_active, 'usage_count': len(transactions),
            'unique_clients': len({row.client_id for row in transactions}),
            'total_points_awarded': sum(row.points_amount for row in transactions if row.points_amount > 0),
            'total_cashback_awarded': amount, 'cashback_currency': currency,
            'cashback_by_currency': [{'currency': unit, 'amount': buckets[unit]['earned']} for unit in _ordered(buckets)],
        })
    return sorted(result, key=lambda row: row['usage_count'], reverse=True)


def get_points_usage_trends(db: Session, business_id: int,
                           date_from: Optional[datetime] = None,
                           date_to: Optional[datetime] = None,
                           group_by: str = 'day') -> List[Dict[str, Any]]:
    grouped = {}
    for transaction in _transactions(db, business_id, date_from, date_to):
        instant = transaction.created_at
        if group_by == 'day':
            key = instant.date().isoformat()
        elif group_by == 'week':
            key = (instant.date() - timedelta(days=instant.weekday())).isoformat()
        else:
            key = f'{instant.year}-{instant.month:02d}'
        grouped.setdefault(key, []).append(transaction)
    result = []
    for period, transactions in sorted(grouped.items()):
        buckets = _flows(transactions)
        earned, currency = _scalar(buckets, 'earned')
        spent, _ = _scalar(buckets, 'spent')
        result.append({
            'period': period,
            'points_earned': sum(row.points_amount for row in transactions if row.transaction_type == 'earned' and row.points_amount > 0),
            'points_spent': abs(sum(row.points_amount for row in transactions if row.transaction_type == 'spent' and row.points_amount < 0)),
            'cashback_earned': earned, 'cashback_spent': spent,
            'transaction_count': len(transactions), 'cashback_currency': currency,
            'cashback_by_currency': [{'currency': unit, 'earned': buckets[unit]['earned'], 'spent': buckets[unit]['spent']}
                                     for unit in _ordered(buckets)],
        })
    return result
