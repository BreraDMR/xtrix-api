"""Explicit price/currency changes use the last read quote and existing settlement rules."""
from decimal import Decimal

import pytest
from sqlalchemy import select,update
from sqlalchemy.orm import Session

from app.api.core.i18n import _
from app.db.models.business_audit_event import BusinessAuditEvent
from app.db.models.service_payment import ServicePaymentAttempt
from app.db.models.visit import Visit
from tests import test_completed_payment_guard as base
from tests import test_visit_reprice_guard_api as guards
from tests.test_visitor_pending_payments import add_attempt

salon=base.salon


def url(visit_id):return f'/visits/{visit_id}/price'
def private(r,status):
    assert r.status_code==status,r.text
    assert r.headers['cache-control']=='no-store'


def payload(quote,currency='EUR'):
    return {'currency':currency,'expected_quote':quote['expected_quote'],
        'items':[{'item_id':item['item_id'],'price':'20.00'} for item in quote['items']]}


def test_write_quote_is_explicit_atomic_and_stale_retries_do_not_duplicate_audit(client,db,salon):
    visit_id,_items=base.create_quote(client,salon)
    old=client.get(url(visit_id),headers=salon['headers']);private(old,200)
    body=payload(old.json());changed=client.put(url(visit_id),headers=salon['headers'],json=body);private(changed,200)
    assert changed.json()['currency']=='EUR' and Decimal(changed.json()['total'])==40
    private(client.put(url(visit_id),headers=salon['headers'],json=body),409)
    assert client.get(url(visit_id),headers=salon['headers']).json()==changed.json()
    same=payload(changed.json());private(client.put(url(visit_id),headers=salon['headers'],json=same),200)
    assert db.query(BusinessAuditEvent).filter_by(action='visit.repriced',entity_id=visit_id).count()==1


@pytest.mark.parametrize('kind',['manual','paid','refunded','reconcile','pending','earned','spent'])
def test_recorded_settlement_needs_correction_and_pending_needs_resolution(client,db,salon,kind):
    visit_id,_items=base.create_quote(client,salon,single=True);guards.evidence(db,salon,visit_id,kind)
    quote=client.get(url(visit_id),headers=salon['headers']);private(quote,200)
    before=base.snapshot(db,visit_id)
    r=client.put(url(visit_id),headers={**salon['headers'],'Accept-Language':'ru'},json=payload(quote.json()))
    private(r,409)
    code='visit_price_payment_pending' if kind=='pending' else 'visit_price_settlement_required'
    assert r.json()['error']['message']==_(code,'ru')
    assert base.snapshot(db,visit_id)==before


@pytest.mark.parametrize('locale',['en','ru','cz'])
def test_stale_quote_has_localized_refusal(client,db,salon,locale):
    visit_id,_items=base.create_quote(client,salon)
    quote=client.get(url(visit_id),headers=salon['headers']).json();body=payload(quote)
    db.execute(update(Visit).where(Visit.id==visit_id).values(price=123));db.commit()
    r=client.put(url(visit_id),headers={**salon['headers'],'Accept-Language':locale},json=body)
    private(r,409);assert r.json()['error']['message']==_('visit_price_quote_changed',locale)


def test_unknown_historical_unit_can_only_be_set_by_explicit_replacement(client,db,salon):
    visit_id,_items=base.create_quote(client,salon,single=True)
    db.execute(update(Visit).where(Visit.id==visit_id).values(currency_snapshot=None));db.commit()
    quote=client.get(url(visit_id),headers=salon['headers']).json();assert quote['currency'] is None
    r=client.put(url(visit_id),headers=salon['headers'],json=payload(quote));private(r,200)
    assert r.json()['currency']=='EUR' and Decimal(r.json()['total'])==20


def test_foreign_item_id_is_rejected_without_partial_prices(client,db,salon):
    visit_id,_items=base.create_quote(client,salon,single=True)
    quote=client.get(url(visit_id),headers=salon['headers']).json();body=payload(quote)
    body['items'][0]['item_id']=999999;before=base.snapshot(db,visit_id)
    private(client.put(url(visit_id),headers=salon['headers'],json=body),422)
    assert base.snapshot(db,visit_id)==before


def test_closed_attempt_lock_is_a_private_retry_without_price_change(client,db,salon):
    visit_id,_items=base.create_quote(client,salon,single=True)
    quote=client.get(url(visit_id),headers=salon['headers']).json()
    aid=add_attempt(db,{'business':salon['business']['id'],'visit':visit_id},'explicit-busy','failed');db.commit()
    before=base.snapshot(db,visit_id)
    with Session(db.get_bind()) as holder:
        holder.execute(select(ServicePaymentAttempt.id).where(ServicePaymentAttempt.id==aid).with_for_update())
        response=client.put(url(visit_id),headers={**salon['headers'],'Accept-Language':'ru'},json=payload(quote))
        private(response,409);assert response.json()['error']['message']==_('visit_price_retry','ru')
    assert base.snapshot(db,visit_id)==before


from tests import test_staff_permissions as staff
staff_salon=staff.salon


def test_staff_without_financial_rights_cannot_replace_quote(client,db,staff_salon):
    w=staff_salon;staff._set_rights(client,w,staff.NO_RIGHTS)
    created=client.post('/visits/',headers=w['h'],json={**staff._visit_body(w),'price':12});assert created.status_code==200,created.text
    visit_id=created.json()['id'];quote=client.get(url(visit_id),headers=w['h']).json()
    private(client.put(url(visit_id),headers=w['eh'],json=payload(quote)),403)
    private(client.put(url(visit_id),json=payload(quote)),401)
    db.expire_all();assert db.get(Visit,visit_id).price==12


@pytest.mark.parametrize('locale',['en','ru','cz'])
def test_invalid_explicit_amount_has_stable_validation_code_without_any_price_update(client,db,salon,locale):
    visit_id,_items=base.create_quote(client,salon,single=True);quote=client.get(url(visit_id),headers=salon['headers']).json()
    body=payload(quote);body['items'][0]['price']='-1.00';before=base.snapshot(db,visit_id)
    r=client.put(url(visit_id),headers={**salon['headers'],'Accept-Language':locale},json=body);private(r,422)
    errors=r.json()['error']['detail']['validation_errors']
    # Validation uses the shared code/params contract; the client translates it.
    assert errors[0]['code']=='visit_price_invalid'
    assert 'input' not in errors[0]
    assert base.snapshot(db,visit_id)==before


def test_committed_quote_stays_successful_if_cache_refresh_fails(client,db,salon,monkeypatch):
    from app.api.core import redis_client
    visit_id,_items=base.create_quote(client,salon,single=True);quote=client.get(url(visit_id),headers=salon['headers']).json()
    def fail(*args):raise RuntimeError('synthetic cache outage')
    monkeypatch.setattr(redis_client,'invalidate_visit',fail)
    private(client.put(url(visit_id),headers=salon['headers'],json=payload(quote)),200)
    with Session(db.get_bind()) as observer:assert observer.get(Visit,visit_id).currency_snapshot=='EUR'


def test_deferred_commit_failure_returns_no_success_and_keeps_old_quote(client,db,salon):
    from sqlalchemy import text
    from fastapi.testclient import TestClient
    from app.main import app
    visit_id,_items=base.create_quote(client,salon,single=True);quote=client.get(url(visit_id),headers=salon['headers']).json()
    before=base.snapshot(db,visit_id)
    db.execute(text('''CREATE FUNCTION reject_http_reprice_commit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
        IF NEW.currency_snapshot='EUR' THEN RAISE EXCEPTION 'synthetic deferred quote rejection'; END IF; RETURN NEW; END $$'''))
    db.execute(text('''CREATE CONSTRAINT TRIGGER reject_http_reprice_commit AFTER UPDATE ON visits
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION reject_http_reprice_commit()'''));db.commit()
    try:
        with TestClient(app,raise_server_exceptions=False) as transport:
            private(transport.put(url(visit_id),headers=salon['headers'],json=payload(quote)),500)
        assert base.snapshot(db,visit_id)==before
    finally:
        db.rollback();db.execute(text('DROP TRIGGER reject_http_reprice_commit ON visits'))
        db.execute(text('DROP FUNCTION reject_http_reprice_commit()'));db.commit()
