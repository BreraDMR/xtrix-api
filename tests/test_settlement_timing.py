"""Payment and price correction meeting in time.

The rule (Damir, 13.11): a payment keeps the amount it was sent with. A late webhook pays that
snapshot, not whatever the price became meanwhile; a correction never touches it. While the
payment is still in flight the price can't be corrected, and two writes on one visit at the
same moment never interleave - the loser is told to retry (409) and nothing of it is saved."""
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.core.clock import utcnow
from app.api.core.i18n import _
from app.db.models.service_payment import ServicePaymentAttempt, ServicePaymentEvent
from app.db.models.visit import Visit
from app.db.models.visit_settlement_entry import VisitSettlementEntry
from tests import test_completed_payment_guard as base
from tests import test_visit_price_corrections as corrections

salon = base.salon


def sent_payment(db, salon, visit_id, amount, key='late-pay'):
    # the checkout went out with this amount; the provider hasn't answered yet
    now = utcnow()
    row = ServicePaymentAttempt(business_id=salon['business']['id'], visit_id=visit_id, provider='sandbox',
        merchant='test', request_key=key, public_ref=key, public_token_hash='0' * 64, status='pending',
        currency='CZK', amount=Decimal(amount), remaining_amount=Decimal('0.00'), refunded_amount=0,
        provider_ref='provider-' + key, expires_at=now + timedelta(hours=1))
    db.add(row)
    db.commit()
    return row.id


def webhook_paid(db, attempt_id):
    row = db.get(ServicePaymentAttempt, attempt_id)
    row.status, row.paid_at = 'paid', utcnow()
    db.add(ServicePaymentEvent(attempt_id=row.id, business_id=row.business_id, provider='sandbox',
        provider_event_id='paid-' + row.request_key, event_type='payment.paid', status='paid',
        source='webhook', payload_hash='1' * 64))
    db.commit()


def another_visit(client, salon, hours):
    r = client.post('/visits/', headers=salon['headers'], json={
        'client_id': salon['client']['id'], 'specialist_id': salon['owner'].id,
        'date_start': (salon['start'] + timedelta(hours=3 * hours)).isoformat(),
        'items': [{'service_id': salon['first']['id']}]})
    assert r.status_code == 200, r.text
    return r.json()['id']


def settlement(client, salon, visit_id):
    r = client.get(f'/visits/{visit_id}/settlement', headers=salon['headers'])
    assert r.status_code == 200, r.text
    return r.json()


def total(client, salon, visit_id):
    return Decimal(corrections.quote(client, salon, visit_id)['total'])


def correction(client, salon, visit_id, *, currency='CZK', price='500.00', key='timing-corr-01'):
    return client.post(corrections.url(visit_id), headers=salon['headers'], json=corrections.body(
        corrections.quote(client, salon, visit_id), currency=currency, price=price, key=key,
        action='retained', reason='Price agreed at the desk'))


def receipt(client, salon, visit_id, amount='5.00', currency='CZK', key='timing-entry-01'):
    return client.post(f'/visits/{visit_id}/settlement-entries', headers=salon['headers'], json={
        'kind': 'manual_receipt', 'amount': amount, 'currency': currency,
        'idempotency_key': key, 'reason': 'Cash at the desk'})


def test_late_payment_pays_the_amount_it_was_sent_with(client, db, salon):
    visit_id, _items = base.create_quote(client, salon, single=True)
    price = total(client, salon, visit_id)
    attempt_id = sent_payment(db, salon, visit_id, '15.00')

    # while the money is on its way there is no balance and no correction
    data = settlement(client, salon, visit_id)
    assert data['outstanding'] is None and data['outstanding_reason'] == 'payment_pending'
    r = correction(client, salon, visit_id)
    assert r.status_code == 409 and r.json()['error']['message'] == _('visit_price_payment_pending', 'en')
    assert total(client, salon, visit_id) == price

    # the provider answers late: exactly the sent snapshot is counted
    webhook_paid(db, attempt_id)
    data = settlement(client, salon, visit_id)
    assert Decimal(data['provider_net']) == Decimal('15.00')
    assert Decimal(data['outstanding']) == price - Decimal('15.00') and data['outstanding_reason'] is None

    # now the price is corrected; the paid snapshot stays as it was
    r = correction(client, salon, visit_id, price='25.00')
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(ServicePaymentAttempt, attempt_id).amount == Decimal('15.00')
    data = settlement(client, salon, visit_id)
    assert (Decimal(data['outstanding']), Decimal(data['overpayment'])) == (Decimal('10.00'), 0)

    # corrected below what was paid: the difference shows as overpayment, not a negative debt
    r = correction(client, salon, visit_id, price='12.00', key='timing-corr-02')
    assert r.status_code == 200, r.text
    data = settlement(client, salon, visit_id)
    assert (Decimal(data['outstanding']), Decimal(data['overpayment'])) == (0, Decimal('3.00'))


def test_receipt_waits_while_a_correction_holds_the_visit(client, db, salon):
    visit_id, _items = base.create_quote(client, salon, single=True)
    assert receipt(client, salon, visit_id, key='timing-entry-00').status_code == 200
    with Session(db.get_bind()) as holder:
        # a correction is being saved right now
        holder.execute(select(Visit.id).where(Visit.id == visit_id).with_for_update())
        r = receipt(client, salon, visit_id)
        assert r.status_code == 409 and r.json()['error']['message'] == _('visit_price_retry', 'en'), r.text
    assert db.query(VisitSettlementEntry).filter_by(visit_id=visit_id).count() == 1
    # it went to euro meanwhile: a crown receipt no longer fits, a euro one does
    assert correction(client, salon, visit_id, currency='EUR', price='40.00').status_code == 200
    r = receipt(client, salon, visit_id)
    assert r.status_code == 409 and r.json()['error']['message'] == _('visit_settlement_currency_mismatch', 'en')
    assert receipt(client, salon, visit_id, amount='10.00', currency='EUR', key='timing-entry-02').status_code == 200


def test_either_order_of_correction_and_receipt_is_explained(client, db, salon):
    # the row lock turns "at the same moment" into one of these two orders (see the test above)
    cash_first, _items = base.create_quote(client, salon, single=True)
    assert receipt(client, salon, cash_first, key='order-cash-01').status_code == 200
    assert correction(client, salon, cash_first, currency='EUR', price='40.00', key='order-corr-01').status_code == 200
    data = settlement(client, salon, cash_first)
    # crowns taken before the switch to euro are never netted against the euro price
    assert data['currency'] == 'EUR' and data['outstanding'] is None
    assert data['outstanding_reason'] is not None

    fix_first = another_visit(client, salon, 1)
    assert receipt(client, salon, fix_first, key='order-seed-02').status_code == 200
    assert correction(client, salon, fix_first, currency='EUR', price='40.00', key='order-corr-02').status_code == 200
    late = receipt(client, salon, fix_first, key='order-cash-02')
    assert late.status_code == 409 and late.json()['error']['message'] == _('visit_settlement_currency_mismatch', 'en')
    assert db.query(VisitSettlementEntry).filter_by(visit_id=fix_first, idempotency_key='order-cash-02').count() == 0
