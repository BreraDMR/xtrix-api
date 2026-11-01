"""Read immutable settlement facts; never infer a payable balance from receipts."""
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import String, cast, func, literal, select
from sqlalchemy.dialects.postgresql import JSONB

from app.api.core import cashback_wallets as money
from app.db.models.gift import GiftCertificate, GiftLedgerEntry
from app.db.models.loyalty_program import LoyaltyTransaction
from app.db.models.service_payment import ServicePaymentAttempt, ServicePaymentEvent, ServicePaymentRefund
from app.db.models.visit import Visit
from app.schemas.limits import ISO_4217


@dataclass(frozen=True)
class SettlementScope:
    """An already authorized scalar target, not an ORM identity-map hint."""
    id: int
    business_id: int
    client_id: int
    specialist_id: int

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in
               (self.id, self.business_id, self.client_id, self.specialist_id)):
            raise ValueError('An authorized positive scalar scope is required')


@dataclass(frozen=True)
class SettlementFacts:
    visit_id: int
    currency: str | None
    gross_quote: Decimal | None
    loyalty_discount: Decimal | None
    price_after_loyalty: Decimal | None
    provider_received: Decimal | None
    provider_refunded: Decimal | None
    provider_net: Decimal | None
    gift_money_coverage: Decimal | None
    gift_units_coverage: int | None
    manual_payment_recorded: bool
    has_pending_checkout: bool
    has_reconciliation: bool
    requires_review: bool
    reasons: tuple[str, ...]
    outstanding: None = None
    overpayment: None = None
    outstanding_reason: str = 'outstanding_not_projected'


def _array(*pairs):
    return func.coalesce(func.jsonb_agg(func.jsonb_build_object(*pairs)), literal([], type_=JSONB))


def _snapshot(scope):
    a, e, r, g, c, l = (ServicePaymentAttempt, ServicePaymentEvent, ServicePaymentRefund,
                        GiftLedgerEntry, GiftCertificate, LoyaltyTransaction)
    # Decimal values inside JSON must be text: the driver's JSON float decoder loses cents.
    attempts = select(_array('id', a.id, 'business', a.business_id, 'provider', a.provider,
        'status', a.status, 'currency', a.currency, 'amount', cast(a.amount, String),
        'refunded', cast(a.refunded_amount, String), 'paid', a.paid_at.is_not(None))).where(
            a.visit_id == Visit.id).correlate(Visit).scalar_subquery()
    events = select(_array('attempt', e.attempt_id, 'business', e.business_id, 'provider', e.provider,
        'type', e.event_type, 'status', e.status, 'source', e.source)).select_from(e).join(a, a.id == e.attempt_id).where(
            a.visit_id == Visit.id).correlate(Visit).scalar_subquery()
    refunds = select(_array('attempt', r.attempt_id, 'business', r.business_id, 'provider', r.provider,
        'currency', r.currency, 'amount', cast(r.amount, String), 'status', r.status)).select_from(r).join(
            a, a.id == r.attempt_id).where(a.visit_id == Visit.id).correlate(Visit).scalar_subquery()
    gifts = select(_array('id', g.id, 'certificate', g.certificate_id, 'business', g.business_id,
        'type', g.entry_type, 'amount', cast(g.amount_delta, String), 'units', g.units_delta,
        'reverses', g.redeems_entry_id, 'certificate_business', c.business_id, 'kind', c.kind,
        'currency', c.currency)).select_from(g).outerjoin(c, c.id == g.certificate_id).where(
            g.visit_id == Visit.id).correlate(Visit).scalar_subquery()
    loyalty = select(_array('business', l.business_id, 'client', l.client_id, 'type', l.transaction_type,
        'points', cast(l.points_amount, String), 'cashback', cast(l.cashback_amount, String),
        'cashback_exact', cast(l.cashback_amount_exact, String), 'currency', l.discount_currency,
        'discount', cast(l.discount_amount_exact, String))).where(
            l.visit_id == Visit.id).correlate(Visit).scalar_subquery()
    return select(Visit.id, Visit.business_id, Visit.client_id, Visit.currency_snapshot, Visit.price,
        Visit.manual_payment_recorded_at, attempts.label('attempts'), events.label('events'),
        refunds.label('refunds'), gifts.label('gifts'), loyalty.label('loyalty')).where(
            Visit.id == scope.id, Visit.business_id == scope.business_id, Visit.client_id == scope.client_id,
            Visit.specialist_id == scope.specialist_id, Visit.deleted_at.is_(None))


def _cents(value, *, signed=False):
    value = money._amount(value)
    if not signed and value < 0:
        raise money.CashbackError('settlement_amount_invalid')
    return money._cents(value)


def _total(value):
    result = money._from_cents(value)
    money._amount(result)
    return result


def _loyalty(rows, business, client, currency, gross):
    cents = 0
    active = False
    try:
        for row in rows:
            if row['business'] != business or row['client'] != client:
                raise ValueError
            if row['type'] != 'spent':
                continue
            used = any(value is not None and Decimal(value) != 0 for value in
                       (row['points'], row['cashback'], row['cashback_exact'], row['discount']))
            if not used:
                continue
            active = True
            if currency is None or row['currency'] != currency:
                raise ValueError
            value = _cents(row['discount'])
            if value <= 0:
                raise ValueError
            cents += value
        if currency is None or gross is None:
            raise ValueError
        discount = _total(cents)
        if cents > gross:
            return discount, None, True, active
        return discount, _total(gross-cents), False, active
    except (ValueError, money.CashbackError, ArithmeticError):
        return None, None, True, active


def _provider(attempts, events, refunds, business, currency):
    received = refunded = 0
    received_known = refunded_known = True
    status_consistent = True
    pending = reconciliation = False
    event_groups = {}
    refund_groups = {}
    for event in events:
        event_groups.setdefault(event['attempt'], []).append(event)
    for refund in refunds:
        refund_groups.setdefault(refund['attempt'], []).append(refund)
    for attempt in attempts:
        rows = event_groups.get(attempt['id'], [])
        returns = refund_groups.get(attempt['id'], [])
        pending |= attempt['status'] == 'pending'
        reconciliation |= attempt['status'] == 'reconcile' or any(
            row['type'] == 'payment.paid' and row['status'] == 'reconcile' for row in rows)
        try:
            if (attempt['business'] != business or currency is None or attempt['currency'] != currency
                    or any(row['business'] != business or row['provider'] != attempt['provider'] for row in rows)):
                raise ValueError
            if any(row['type'] not in ('payment.paid', 'payment.failed', 'payment.pending', 'refund.succeeded')
                   or row['source'] != ('internal' if row['type'] == 'refund.succeeded' else 'webhook')
                   or (row['type'] == 'payment.paid' and row['status'] not in
                       ('paid', 'partially_refunded', 'refunded', 'reconcile')) for row in rows):
                raise ValueError
            value = _cents(attempt['amount'])
            if value <= 0:
                raise ValueError
        except (ValueError, money.CashbackError):
            received_known = refunded_known = False
            continue
        aggregate = None
        try:
            aggregate = _cents(attempt['refunded'])
            returned = 0
            for row in returns:
                if (row['business'] != business or row['provider'] != attempt['provider']
                        or row['currency'] != currency or row['status'] != 'succeeded'):
                    raise ValueError
                part = _cents(row['amount'])
                if part <= 0:
                    raise ValueError
                returned += part
            if returned != aggregate or returned > value:
                raise ValueError
            if len(returns) != sum(row['type'] == 'refund.succeeded' for row in rows):
                raise ValueError
            refunded += returned
        except (ValueError, money.CashbackError):
            refunded_known = False
        paid_events = [row for row in rows if row['type'] == 'payment.paid']
        if attempt['status'] in ('paid', 'partially_refunded', 'refunded'):
            if not paid_events or not attempt['paid'] or any(row['status'] == 'reconcile' for row in paid_events):
                received_known = False
            else:
                received += value
            if (aggregate is None or (attempt['status'] == 'paid' and aggregate != 0)
                    or (attempt['status'] == 'partially_refunded' and not 0 < aggregate < value)
                    or (attempt['status'] == 'refunded' and aggregate != value)):
                status_consistent = False
        elif attempt['status'] not in ('pending', 'failed', 'expired', 'cancelled'):
            received_known = False
        elif paid_events or attempt['paid'] or aggregate or returns:
            received_known = False
    try:
        received_value = _total(received) if received_known else None
        refunded_value = _total(refunded) if refunded_known else None
        net = _total(received-refunded) if received_known and refunded_known and status_consistent and received >= refunded else None
    except money.CashbackError:
        return None, None, None, pending, reconciliation
    return received_value, refunded_value, net, pending, reconciliation


def _gifts(rows, business, currency):
    entries = {row['id']: row for row in rows}
    reversals = set()
    money_cents = units = 0
    try:
        for row in rows:
            if row['business'] != business or row['certificate_business'] != business:
                raise ValueError
            value = _cents(row['amount'], signed=True)
            count = row['units']
            if type(count) is not int or row['kind'] not in ('money', 'package'):
                raise ValueError
            if row['kind'] == 'money':
                if currency is None or row['currency'] != currency or count != 0:
                    raise ValueError
            elif value != 0:
                raise ValueError
            if row['type'] == 'redeemed':
                if row['reverses'] is not None or (row['kind'] == 'money' and value >= 0) or (
                        row['kind'] == 'package' and count != -1):
                    raise ValueError
            elif row['type'] == 'reversed':
                original = entries.get(row['reverses'])
                if (original is None or original['type'] != 'redeemed'
                        or original['certificate'] != row['certificate'] or row['reverses'] in reversals
                        or value != -_cents(original['amount'], signed=True) or count != -original['units']):
                    raise ValueError
                reversals.add(row['reverses'])
            else:
                raise ValueError
            money_cents -= value
            units -= count
        if money_cents < 0 or units < 0:
            raise ValueError
        return _total(money_cents), units, False
    except (ValueError, money.CashbackError):
        return None, None, True


def read_settlement(db, scope: SettlementScope) -> SettlementFacts | None:
    """One fresh SQL snapshot, no writes/flush/commit/locks or permission fallback."""
    if not isinstance(scope, SettlementScope):
        raise ValueError('An explicit authorized scalar scope is required')
    with db.no_autoflush:
        row = db.execute(_snapshot(scope)).mappings().one_or_none()
    if row is None:
        return None
    reasons = set()
    currency = row['currency_snapshot'] if row['currency_snapshot'] in ISO_4217 else None
    if currency is None:
        reasons.add('quote_currency_unknown')
    try:
        gross = _cents(row['price'])
        gross_value = _total(gross)
    except money.CashbackError:
        gross = gross_value = None
        reasons.add('quote_amount_invalid')
    discount, price_after, loyalty_review, loyalty_active = _loyalty(
        row['loyalty'], scope.business_id, scope.client_id, currency, gross)
    if loyalty_review:
        reasons.add('loyalty_history_review')
    received, refunded, net, pending, reconciliation = _provider(
        row['attempts'], row['events'], row['refunds'], scope.business_id, currency)
    if received is None or refunded is None or net is None:
        reasons.add('provider_history_review')
    gift_money, gift_units, gift_review = _gifts(row['gifts'], scope.business_id, currency)
    if gift_review:
        reasons.add('gift_history_review')
    if gift_units:
        reasons.add('gift_package_value_unknown')
    manual = row['manual_payment_recorded_at'] is not None
    if manual:
        reasons.add('manual_amount_unknown')
    if sum((bool(row['attempts']), bool(row['gifts']), bool(loyalty_active), manual)) > 1:
        reasons.add('mixed_settlement_history')
    return SettlementFacts(row['id'], currency, gross_value, discount, price_after,
        received, refunded, net, gift_money, gift_units, manual, pending, reconciliation,
        bool(reasons), tuple(sorted(reasons)))
