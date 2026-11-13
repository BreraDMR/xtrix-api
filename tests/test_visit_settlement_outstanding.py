"""What is still owed on a visit: only when every amount is known and in one currency."""
from decimal import Decimal, Inexact, Rounded, localcontext

import pytest
from sqlalchemy import event, update

from app.api.core.i18n import _
from app.api.core.visit_settlement_projection import read_settlement
from app.db.models.business import Business
from app.db.models.service_payment import ServicePaymentAttempt
from app.db.models.visit import Visit
from app.db.models.visit_settlement_entry import VisitSettlementEntry
from tests import test_completed_payment_guard as base
from tests import test_visit_price_corrections as corrections
from tests import test_visit_reprice_guard_api as guards
from tests import test_visit_settlement_projection as facts

world = facts.world
salon = base.salon
_count = [0]


def entry(db, w, kind, amount, currency='EUR', business_id=None):
    _count[0] += 1
    scope = w['scope']
    row = VisitSettlementEntry(business_id=business_id or scope.business_id, visit_id=scope.id, kind=kind,
        amount=Decimal(amount), currency=currency, reason='projection test',
        idempotency_key=f'proj-entry-{_count[0]:04d}', request_hash='b' * 64, created_at=facts.NOW)
    db.add(row)
    db.commit()
    return row


def cancel(db, w):
    db.execute(update(Visit).where(Visit.id == w['scope'].id).values(status='cancelled'))
    db.commit()


def balance(db, w):
    result = read_settlement(db, w['scope'])
    return result.outstanding, result.overpayment, result.outstanding_reason


# ---- the plain cases ----

def test_unpaid_known_quote_is_owed_in_full(db, world):
    result = read_settlement(db, world['scope'])
    assert (result.outstanding, result.overpayment, result.outstanding_reason) == (Decimal('1000.01'), 0, None)
    assert result.charges == result.manual_received == result.manual_refunded == 0


def test_manual_receipt_keeps_exact_amount_and_currency(db, world):
    entry(db, world, 'manual_receipt', '400.01')
    result = read_settlement(db, world['scope'])
    assert result.currency == 'EUR' and result.manual_received == Decimal('400.01')
    assert (result.outstanding, result.overpayment) == (Decimal('600.00'), 0)


def test_partial_receipts_add_up_and_overpayment_is_shown_apart(db, world):
    entry(db, world, 'manual_receipt', '600.00')
    entry(db, world, 'manual_receipt', '400.01')
    assert balance(db, world) == (0, 0, None)
    entry(db, world, 'manual_receipt', '0.99')
    assert balance(db, world) == (0, Decimal('0.99'), None)
    # giving the extra back settles it, nothing new is owed
    entry(db, world, 'manual_refund', '0.99')
    assert balance(db, world) == (0, 0, None)


def test_refund_never_brings_a_debt_back_by_itself(db, world):
    entry(db, world, 'manual_receipt', '1000.01')
    entry(db, world, 'manual_refund', '100.00')
    outstanding, overpayment, reason = balance(db, world)
    assert outstanding is None and overpayment is None and reason == 'refund_balance_review'


def test_provider_refund_after_full_payment_is_review_not_debt(db, world):
    row = facts.attempt(db, world, value='1000.01')
    facts.refund(db, row, value='10.00')
    assert balance(db, world) == (None, None, 'refund_balance_review')


def test_explicit_charge_adds_to_the_price(db, world):
    entry(db, world, 'charge', '50.00')
    facts.attempt(db, world, value='1000.01')
    result = read_settlement(db, world['scope'])
    assert result.charges == 50 and result.provider_net == Decimal('1000.01')
    assert (result.outstanding, result.overpayment, result.outstanding_reason) == (Decimal('50.00'), 0, None)


# ---- cancellation (decision 30.10: no debt without an explicit charge) ----

def test_cancelled_visit_without_charge_owes_nothing(db, world):
    cancel(db, world)
    assert balance(db, world) == (0, 0, None)


def test_cancelled_visit_owes_only_its_explicit_charge(db, world):
    cancel(db, world)
    entry(db, world, 'charge', '30.00')
    assert balance(db, world) == (Decimal('30.00'), 0, None)
    entry(db, world, 'manual_receipt', '30.00')
    assert balance(db, world) == (0, 0, None)


def test_cancelled_after_refund_does_not_revive_the_price(db, world):
    row = facts.attempt(db, world, value='20.01')
    cancel(db, world)
    assert balance(db, world) == (0, Decimal('20.01'), None)
    facts.refund(db, row, value='20.01')
    db.execute(update(ServicePaymentAttempt).where(ServicePaymentAttempt.id == row.id).values(status='refunded'))
    db.commit()
    assert balance(db, world) == (0, 0, None)


def test_cancelled_visit_with_used_certificate_is_review(db, world):
    facts.gift(db, world, value='7.01')
    cancel(db, world)
    assert balance(db, world) == (None, None, 'cancelled_coverage_review')


# ---- unknowns stay unknown ----

def test_old_amountless_marker_blocks_even_with_exact_receipt(db, client, salon):
    visit_id, _items = base.create_quote(client, salon)
    guards.evidence(db, salon, visit_id, 'manual')
    data = client.get(f'/visits/{visit_id}/settlement', headers=salon['headers']).json()
    assert data['outstanding'] is None and data['outstanding_reason'] == 'manual_amount_unknown'


def test_unknown_quote_currency_has_no_balance(db, world):
    db.execute(update(Visit).where(Visit.id == world['scope'].id).values(currency_snapshot=None))
    db.commit()
    assert balance(db, world) == (None, None, 'quote_currency_unknown')


def test_pending_checkout_waits_for_the_provider(db, world):
    facts.attempt(db, world, status='pending', paid=False)
    result = read_settlement(db, world['scope'])
    assert result.has_pending_checkout
    assert (result.outstanding, result.outstanding_reason) == (None, 'payment_pending')


def test_entry_in_another_currency_without_correction_is_review(db, world):
    entry(db, world, 'manual_receipt', '10.00', currency='CZK')
    result = read_settlement(db, world['scope'])
    assert 'settlement_entry_review' in result.reasons and result.outstanding is None


def test_entry_of_another_business_is_never_counted(db, world, make_user):
    other = Business(name='Other', business_type='salon', currency='EUR')
    db.add(other)
    db.commit()
    entry(db, world, 'manual_receipt', '10.00', business_id=other.id)
    result = read_settlement(db, world['scope'])
    assert 'settlement_entry_review' in result.reasons and result.outstanding is None


# ---- corrections between currencies ----

def test_same_currency_correction_keeps_the_balance(db, world):
    entry(db, world, 'manual_receipt', '100.00')
    corrections.corrected(db, world, old='EUR', new='EUR')
    assert balance(db, world) == (Decimal('900.01'), 0, None)


def test_prior_currency_money_blocks_until_returned(db, world):
    row = facts.attempt(db, world, value='20.01')
    corrections.corrected(db, world)
    result = read_settlement(db, world['scope'])
    assert result.currency == 'CZK' and result.outstanding is None
    assert result.outstanding_reason == 'prior_currency_settlement'
    facts.refund(db, row, value='20.01')
    db.execute(update(ServicePaymentAttempt).where(ServicePaymentAttempt.id == row.id).values(status='refunded'))
    db.commit()
    # old euro money is back with the client, the new crown quote is owed in full
    assert balance(db, world) == (Decimal('1000.01'), 0, None)


def test_prior_currency_manual_entries_are_reported_apart(db, world):
    entry(db, world, 'manual_receipt', '5.00')
    corrections.corrected(db, world)
    result = read_settlement(db, world['scope'])
    assert result.manual_received == 0 and result.outstanding is None
    assert 'prior_currency_entries' in result.reasons
    [prior] = result.prior_currency_settlements
    assert prior.currency == 'EUR' and prior.entry_rows == 1


# ---- benefits and cash together, each counted once ----

def test_bonus_certificate_provider_and_receipt_together(db, world):
    facts.bonus(db, world, value='10.01')
    facts.gift(db, world, value='7.00')
    facts.attempt(db, world, value='900.00')
    entry(db, world, 'manual_receipt', '80.00')
    result = read_settlement(db, world['scope'])
    assert 'mixed_settlement_history' in result.reasons
    # 1000.01 - 10.01 bonus - 7.00 gift - 900.00 card - 80.00 cash
    assert (result.outstanding, result.overpayment, result.outstanding_reason) == (Decimal('3.00'), 0, None)


def test_package_with_saved_value_covers_the_visit(db, world):
    facts.gift(db, world, kind='package', coverage=('EUR', '1000.01'))
    assert balance(db, world) == (0, 0, None)


def test_package_without_saved_value_is_unknown(db, world):
    facts.gift(db, world, kind='package')
    assert balance(db, world) == (None, None, 'gift_package_value_unknown')


def test_benefits_over_the_price_are_not_money_to_give_back(db, world):
    facts.bonus(db, world, value='10.01')
    facts.gift(db, world, kind='package', coverage=('EUR', '1000.01'))
    assert balance(db, world) == (None, None, 'coverage_exceeds_price')


# ---- snapshot ----

def test_entries_are_read_in_the_same_single_select(db, world):
    entry(db, world, 'manual_receipt', '0.01')
    statements = []
    engine = db.get_bind()

    def seen(conn, cursor, sql, parameters, context, executemany):
        statements.append(sql)
    event.listen(engine, 'after_cursor_execute', seen)
    try:
        with localcontext() as ctx:
            ctx.prec = 2
            ctx.traps[Inexact] = True
            ctx.traps[Rounded] = True
            result = read_settlement(db, world['scope'])
    finally:
        event.remove(engine, 'after_cursor_execute', seen)
    assert len([sql for sql in statements if sql.lstrip().upper().startswith('SELECT')]) == 1
    assert result.outstanding == Decimal('1000.00')


# ---- API ----

def url(visit_id):
    return f'/visits/{visit_id}/settlement'


def test_api_shows_the_balance_and_replays_count_once(client, db, salon):
    visit_id, _items = base.create_quote(client, salon)
    price = Decimal(client.get(f'/visits/{visit_id}/price', headers=salon['headers']).json()['total'])
    entry_body = {'kind': 'manual_receipt', 'amount': '10.00', 'currency': 'CZK',
                  'idempotency_key': 'balance-key-0001', 'reason': 'Cash at the desk'}
    for _ in range(2):
        assert client.post(f'/visits/{visit_id}/settlement-entries', headers=salon['headers'],
                           json=entry_body).status_code == 200
    r = client.get(url(visit_id), headers=salon['headers'])
    assert r.status_code == 200, r.text
    assert r.headers['cache-control'] == 'no-store'
    data = r.json()
    assert data['currency'] == 'CZK' and Decimal(data['manual_received']) == 10
    assert Decimal(data['outstanding']) == price - 10 and Decimal(data['overpayment']) == 0
    assert data['outstanding_reason'] is None and data['message'] is None


@pytest.mark.parametrize('locale', ['en', 'ru', 'cz'])
def test_api_explains_an_unknown_balance(client, db, salon, locale):
    visit_id, _items = base.create_quote(client, salon)
    guards.evidence(db, salon, visit_id, 'manual')
    data = client.get(url(visit_id), headers={**salon['headers'], 'Accept-Language': locale}).json()
    assert data['outstanding'] is None and data['overpayment'] is None
    assert data['message'] == _('visit_settlement_balance_review', locale)


def test_api_permissions_match_the_price(client, db, salon, make_user, headers_for):
    visit_id, _items = base.create_quote(client, salon)
    staff = client.post('/employees/create-with-user', headers=salon['headers'], json={
        'full_name': 'No money staff', 'email': 'nomoney.balance@example.com', 'base_salary': 0,
        'permissions': {'can_create_visits': True, 'can_view_financials': False}})
    assert staff.status_code == 200, staff.text
    from app.db.models.user import User
    staff_headers = headers_for(db.get(User, staff.json()['employee_user_id']))
    other = make_user('business_owner')
    other_headers = headers_for(other)
    assert client.post('/businesses/', headers=other_headers, json={'name': 'Other', 'business_type': 'salon'}).status_code == 200
    assert client.get(url(visit_id), headers=staff_headers).status_code == 403
    assert client.get(url(visit_id), headers=other_headers).status_code == 404
    assert client.get(url(visit_id)).status_code == 401
    assert client.delete(f'/visits/{visit_id}', headers=salon['headers']).status_code in (200, 204)
    assert client.get(url(visit_id), headers=salon['headers']).status_code == 404
