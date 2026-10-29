"""Read-only runtime probe of guest reschedule after catalog changes."""
import pytest
from datetime import timedelta
from decimal import Decimal
from sqlalchemy import select
from app.api.core.booking_manage_access import issue_token
from app.db.models.service_payment import ServicePaymentAttempt, ServicePaymentEvent
from app.db.models.visit import Visit
from app.db.models.visit_item import VisitItem
from app.db.models.visitor_account import VisitorAccount
from tests import test_multi_service as base

salon = base.salon
_ok = base._ok


@pytest.mark.parametrize("snapshot_currency", ["CZK", None])
def test_guest_summary_uses_known_or_unknown_stored_currency(client, db, salon, snapshot_currency):
    business_id = salon['business']['id']
    owner_id = salon['owner'].id
    _ok(client.put(f'/businesses/{business_id}', headers=salon['headers'], json={'currency': 'CZK'}), 'CZK business')
    result = _ok(client.post('/visits/', headers=salon['headers'], json={
        'client_id': salon['client']['id'], 'specialist_id': owner_id,
        'service_id': salon['first']['id'], 'price': 999,
        'date_start': salon['start'].isoformat()+'Z',
        'date_end': (salon['start']+timedelta(minutes=60)).isoformat()+'Z',
    }), 'ordinary quote')
    visit = db.get(Visit, result['id'])
    raw, _ = issue_token(db, visit)
    if snapshot_currency is None:
        # An old quote with no currency evidence must remain unknown.
        visit.currency_snapshot = None
        db.commit()
    attempt = ServicePaymentAttempt(business_id=business_id, visit_id=visit.id,
        provider='synthetic', merchant='synthetic', request_key='quote-paid', public_ref='quote-paid',
        public_token_hash='a'*64, status='paid', currency='CZK', amount=Decimal('100.00'),
        remaining_amount=Decimal('899.00'), refunded_amount=0,
        expires_at=salon['start']-timedelta(days=1), paid_at=salon['start']-timedelta(days=3))
    db.add(attempt);db.flush()
    db.add(ServicePaymentEvent(attempt_id=attempt.id,business_id=business_id,provider='synthetic',
        provider_event_id='quote-paid-event',event_type='payment.paid',status='paid',payload_hash='b'*64))
    db.commit()
    visit_id = visit.id
    def rows(model):
        return [dict(row) for row in db.execute(select(model.__table__)).mappings()]
    before_visit = dict(db.execute(select(Visit.__table__).where(Visit.id==visit_id)).mappings().one())
    before_items = rows(VisitItem)
    before_attempts = rows(ServicePaymentAttempt)
    before_events = rows(ServicePaymentEvent)
    assert before_visit['price'] == 999 and before_visit['currency_snapshot'] == snapshot_currency
    assert before_items[0]['price'] == Decimal('999.00') and before_items[0]['duration_minutes'] == 60
    assert db.query(VisitorAccount).count() == 0
    db.rollback()
    _ok(client.put(f'/businesses/{business_id}', headers=salon['headers'],json={'currency':'EUR'}), 'current EUR business')
    _ok(client.put(f"/services/{salon['first']['id']}", headers=salon['headers'],json={'price':40,'duration_minutes':15}), 'current catalog')
    for method, suffix in [('get', ''), ('post', '/confirm')]:
        summary = getattr(client, method)('/public/booking-management'+suffix, headers={'X-Booking-Token':raw})
        assert summary.status_code == 200, summary.text
        assert summary.json()['price'] == 999
        assert summary.json()['currency'] == snapshot_currency
    # Confirmation is an independent action; freeze the comparison after it.
    db.expire_all()
    before_visit = dict(db.execute(select(Visit.__table__).where(Visit.id==visit_id)).mappings().one())
    db.rollback()
    target = salon['start']+timedelta(hours=3)
    response = client.post('/public/booking-management/reschedule',headers={'X-Booking-Token':raw},json={'start':target.isoformat()+'Z'})
    assert response.status_code == 200, response.text
    print('HTTP guest move response:',response.json())
    db.expire_all()
    after_visit = dict(db.execute(select(Visit.__table__).where(Visit.id==visit_id)).mappings().one())
    after_items = rows(VisitItem)
    assert {key for key in before_visit if before_visit[key]!=after_visit[key]} == {'date_start','date_end'}
    for before, after in zip(before_items,after_items):
        assert {key for key in before if before[key]!=after[key]} == {'date_start','date_end'}
    assert after_visit['price']==999 and after_visit['currency_snapshot']==snapshot_currency
    assert after_visit['date_end']-after_visit['date_start']==timedelta(minutes=60)
    assert before_attempts==rows(ServicePaymentAttempt)
    assert before_events==rows(ServicePaymentEvent)
    assert db.query(VisitorAccount).count()==0
    print('Persisted quote:',after_visit['price'],after_visit['currency_snapshot'],'60 minutes; item/receipt snapshots unchanged')
    assert response.json()['price']==999
    assert response.json()['currency']==snapshot_currency
    cancelled = client.post('/public/booking-management/cancel', headers={'X-Booking-Token':raw})
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()['currency'] == snapshot_currency
    assert cancelled.json()['price'] == 999
