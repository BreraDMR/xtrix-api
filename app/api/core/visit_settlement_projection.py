"""Read immutable settlement facts and, only when every amount is known, what is still owed.

The balance is a plain sum in one currency: price after bonuses + explicit charges
- certificate/package coverage - provider net - manual receipts + manual refunds.
Anything unknown (old amountless marker, broken history, money left in a replaced
currency, a checkout in flight) gives None and a reason instead of a guess. A
cancelled visit owes only its explicit charges (decision 30.10), and a refund on
its own never turns into a debt.
"""
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import String, cast, func, literal, select
from sqlalchemy.dialects.postgresql import JSONB

from app.api.core import cashback_wallets as money
from app.db.models.gift import GiftCertificate, GiftLedgerEntry
from app.db.models.loyalty_program import LoyaltyTransaction
from app.db.models.service_payment import ServicePaymentAttempt, ServicePaymentEvent, ServicePaymentRefund
from app.db.models.visit import Visit
from app.db.models.visit_price_correction import VisitPriceCorrection
from app.db.models.visit_settlement_entry import VisitSettlementEntry
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
class PriorCurrencySettlement:
    """Money recorded under a quote currency that a later correction replaced.

    Shown on its own, in its own currency: without FX it can't count toward the new quote.
    """
    currency: str
    provider_received: Decimal | None
    provider_refunded: Decimal | None
    provider_net: Decimal | None
    benefit_rows: int
    entry_rows: int = 0


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
    gift_package_coverage: Decimal | None
    manual_payment_recorded: bool
    has_pending_checkout: bool
    has_reconciliation: bool
    requires_review: bool
    reasons: tuple[str, ...]
    prior_currency_settlements: tuple[PriorCurrencySettlement, ...] = ()
    charges: Decimal | None = None
    manual_received: Decimal | None = None
    manual_refunded: Decimal | None = None
    outstanding: Decimal | None = None
    overpayment: Decimal | None = None
    outstanding_reason: str | None = 'outstanding_not_projected'


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
        'currency', c.currency, 'coverage_currency', g.coverage_currency,
        'coverage_amount', cast(g.coverage_amount_exact, String))).select_from(g).outerjoin(c, c.id == g.certificate_id).where(
            g.visit_id == Visit.id).correlate(Visit).scalar_subquery()
    loyalty = select(_array('business', l.business_id, 'client', l.client_id, 'type', l.transaction_type,
        'points', cast(l.points_amount, String), 'cashback', cast(l.cashback_amount, String),
        'cashback_exact', cast(l.cashback_amount_exact, String), 'currency', l.discount_currency,
        'discount', cast(l.discount_amount_exact, String))).where(
            l.visit_id == Visit.id).correlate(Visit).scalar_subquery()
    s = VisitSettlementEntry
    entries = select(_array('business', s.business_id, 'kind', s.kind, 'currency', s.currency,
        'amount', cast(s.amount, String))).where(s.visit_id == Visit.id).correlate(Visit).scalar_subquery()
    k = VisitPriceCorrection
    corrected_from = select(func.coalesce(func.jsonb_agg(k.old_currency), literal([], type_=JSONB))).where(
        k.visit_id == Visit.id, k.business_id == Visit.business_id, k.old_currency.is_not(None)
        ).correlate(Visit).scalar_subquery()
    return select(Visit.id, Visit.business_id, Visit.client_id, Visit.currency_snapshot, Visit.price,
        Visit.status, Visit.manual_payment_recorded_at, attempts.label('attempts'), events.label('events'),
        refunds.label('refunds'), gifts.label('gifts'), loyalty.label('loyalty'), entries.label('entries'),
        corrected_from.label('corrected_from')).where(
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
    money_cents = units = package_cents = unknown_units = 0
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
                if row['coverage_currency'] is not None or row['coverage_amount'] is not None:
                    raise ValueError
            elif value != 0:
                raise ValueError
            coverage = None
            if row['kind'] == 'package' and (row['coverage_currency'] is not None or row['coverage_amount'] is not None):
                if currency is None or row['coverage_currency'] != currency:
                    raise ValueError
                coverage = _cents(row['coverage_amount'])
                if coverage <= 0:
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
                if (row['coverage_currency'] != original['coverage_currency']
                        or (coverage is None) != (original['coverage_amount'] is None)
                        or (coverage is not None and coverage != _cents(original['coverage_amount']))):
                    raise ValueError
                reversals.add(row['reverses'])
            else:
                raise ValueError
            money_cents -= value
            units -= count
            if row['kind'] == 'package':
                if coverage is None:
                    unknown_units -= count
                else:
                    package_cents -= count * coverage
        if money_cents < 0 or not 0 <= units <= 1 or unknown_units < 0 or package_cents < 0:
            raise ValueError
        return _total(money_cents), units, None if unknown_units else _total(package_cents), False
    except (ValueError, money.CashbackError):
        return None, None, None, True


def _entries(rows, business, currency):
    """Charges, manual receipts and manual refunds in cents, or None when a row doesn't fit."""
    totals = {'charge': 0, 'manual_receipt': 0, 'manual_refund': 0}
    try:
        for row in rows:
            if row['business'] != business or currency is None or row['currency'] != currency:
                raise ValueError
            value = _cents(row['amount'])
            if value <= 0 or row['kind'] not in totals:
                raise ValueError
            totals[row['kind']] += value
    except (ValueError, money.CashbackError):
        return None
    return totals


# Several kinds of payment on one visit is worth a look, but when each amount is known
# exactly the sum is still exact - so this one doesn't hide the balance.
_INFORMATIONAL = frozenset({'mixed_settlement_history'})
# root causes go first: with no quote currency every other check fails too
_FIRST = ('quote_currency_unknown', 'quote_amount_invalid', 'manual_amount_unknown')


def _balance(*, cancelled, reasons, pending, reconciliation, price_after, entries,
             gift_money, gift_units, gift_package, provider_received, provider_refunded):
    """(outstanding, overpayment, reason) in cents; both None with a reason when unknown."""
    blocking = sorted(set(reasons) - _INFORMATIONAL, key=lambda code: (code not in _FIRST, _FIRST.index(code)
                      if code in _FIRST else 0, code))
    if blocking:
        return None, None, blocking[0]
    if pending:
        return None, None, 'payment_pending'
    if reconciliation:
        return None, None, 'payment_reconciliation'
    coverage = _cents(gift_money) + _cents(gift_package)
    if cancelled:
        # a cancelled visit owes only what was charged on purpose; a used certificate
        # or package there should be given back by reversing it, not shown as cash
        if coverage or gift_units:
            return None, None, 'cancelled_coverage_review'
        owed = entries['charge']
    else:
        price = _cents(price_after)
        if coverage > price:
            # bonuses and coverage together went past the price: that's not cash to return
            return None, None, 'coverage_exceeds_price'
        owed = price - coverage + entries['charge']
    paid = _cents(provider_received) + entries['manual_receipt']
    returned = _cents(provider_refunded) + entries['manual_refund']
    left = owed - paid + returned
    if returned and left > 0:
        # money went back and something is owed again: the refund alone must not make a debt,
        # a person has to say whether the client really owes it (explicit charge, decision 30.10)
        return None, None, 'refund_balance_review'
    return max(left, 0), max(-left, 0), None


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
    # rows recorded under a currency the visit was corrected away from are reported apart
    prior = {value for value in row['corrected_from'] if value in ISO_4217 and value != currency}
    attempts, events, refunds, gifts, loyalty_rows, entry_rows, prior_rows = _split_prior(row, prior)
    try:
        gross = _cents(row['price'])
        gross_value = _total(gross)
    except money.CashbackError:
        gross = gross_value = None
        reasons.add('quote_amount_invalid')
    discount, price_after, loyalty_review, loyalty_active = _loyalty(
        loyalty_rows, scope.business_id, scope.client_id, currency, gross)
    if loyalty_review:
        reasons.add('loyalty_history_review')
    received, refunded, net, pending, reconciliation = _provider(
        attempts, events, refunds, scope.business_id, currency)
    if received is None or refunded is None or net is None:
        reasons.add('provider_history_review')
    gift_money, gift_units, gift_package, gift_review = _gifts(gifts, scope.business_id, currency)
    if gift_review:
        reasons.add('gift_history_review')
    if gift_units and gift_package is None:
        reasons.add('gift_package_value_unknown')
    manual = row['manual_payment_recorded_at'] is not None
    if manual:
        reasons.add('manual_amount_unknown')
    if sum((bool(attempts), bool(gifts), bool(loyalty_active), manual)) > 1:
        reasons.add('mixed_settlement_history')
    entries = _entries(entry_rows, scope.business_id, currency)
    if entries is None:
        reasons.add('settlement_entry_review')
    prior_facts = []
    for code in sorted(prior_rows):
        part = prior_rows[code]
        p_received, p_refunded, p_net, p_pending, p_reconciliation = _provider(
            part['attempts'], part['events'], part['refunds'], scope.business_id, code)
        pending |= p_pending
        reconciliation |= p_reconciliation
        prior_facts.append(PriorCurrencySettlement(code, p_received, p_refunded, p_net, part['benefits'],
                                                   part['entries']))
        if part['attempts'] and (p_net is None or p_net != 0):
            reasons.add('prior_currency_settlement')
        if part['benefits']:
            reasons.add('prior_currency_benefits')
        if part['entries']:
            # no FX: a manual receipt in the old currency can't pay the new quote
            reasons.add('prior_currency_entries')
    outstanding = overpayment = None
    try:
        left, extra, why = _balance(cancelled=row['status'] == 'cancelled', reasons=reasons, pending=pending,
            reconciliation=reconciliation, price_after=price_after, entries=entries, gift_money=gift_money,
            gift_units=gift_units, gift_package=gift_package, provider_received=received,
            provider_refunded=refunded)
        if why is None:
            outstanding, overpayment = _total(left), _total(extra)
    except (money.CashbackError, ArithmeticError, TypeError):
        why = 'outstanding_review'
    known = entries or {}
    return SettlementFacts(row['id'], currency, gross_value, discount, price_after,
        received, refunded, net, gift_money, gift_units, gift_package, manual, pending, reconciliation,
        bool(reasons), tuple(sorted(reasons)), tuple(prior_facts),
        charges=_total(known['charge']) if entries else None,
        manual_received=_total(known['manual_receipt']) if entries else None,
        manual_refunded=_total(known['manual_refund']) if entries else None,
        outstanding=outstanding, overpayment=overpayment, outstanding_reason=why)


def _split_prior(row, prior):
    """Current-currency rows for the usual checks, and per-currency buckets for the rest."""
    if not prior:
        return row['attempts'], row['events'], row['refunds'], row['gifts'], row['loyalty'], row['entries'], {}
    buckets = {code: {'attempts': [], 'events': [], 'refunds': [], 'benefits': 0, 'entries': 0} for code in prior}
    owner = {}
    attempts = []
    for attempt in row['attempts']:
        if attempt['currency'] in prior:
            owner[attempt['id']] = attempt['currency']
            buckets[attempt['currency']]['attempts'].append(attempt)
        else:
            attempts.append(attempt)
    events, refunds = [], []
    for event in row['events']:
        (buckets[owner[event['attempt']]]['events'] if event['attempt'] in owner else events).append(event)
    for refund in row['refunds']:
        (buckets[owner[refund['attempt']]]['refunds'] if refund['attempt'] in owner else refunds).append(refund)
    gifts = []
    for gift in row['gifts']:
        unit = gift['currency'] if gift['kind'] == 'money' else gift['coverage_currency']
        if unit in prior:
            buckets[unit]['benefits'] += 1
        else:
            gifts.append(gift)
    loyalty = []
    for entry in row['loyalty']:
        if entry['currency'] in prior:
            buckets[entry['currency']]['benefits'] += 1
        else:
            loyalty.append(entry)
    entries = []
    for entry in row['entries']:
        if entry['currency'] in prior:
            buckets[entry['currency']]['entries'] += 1
        else:
            entries.append(entry)
    return attempts, events, refunds, gifts, loyalty, entries, buckets
