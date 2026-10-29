"""The ordinary CRM editor cannot bypass recorded settlement through another field."""
import pytest
from sqlalchemy import update

from app.db.models.visit import Visit
from app.db.models.loyalty_program import LoyaltyTransaction
from tests import test_completed_payment_guard as base
from tests.test_visitor_pending_payments import add_attempt
from app.api.core.i18n import _

salon=base.salon


def evidence(db,salon,visit_id,kind):
    w={'business':salon['business']['id'],'visit':visit_id}
    if kind=='manual':
        db.execute(update(Visit).where(Visit.id==visit_id).values(status='completed',payment_method='cash'));db.commit()
        db.execute(update(Visit).where(Visit.id==visit_id).values(status='planned',payment_method=None))
    elif kind in ('earned','spent'):
        db.add(LoyaltyTransaction(client_id=salon['client']['id'],business_id=w['business'],visit_id=visit_id,
            transaction_type=kind,points_amount=2 if kind=='earned' else -2,points_balance_after=0,cashback_balance_after=0))
    else:add_attempt(db,w,'settled-editor',kind)
    db.commit()


@pytest.mark.parametrize('kind',['manual','paid','refunded','reconcile','pending','earned','spent'])
@pytest.mark.parametrize('change',['price','service'])
def test_existing_editor_refuses_actual_quote_change_after_financial_history(client,db,salon,kind,change):
    visit_id,items=base.create_quote(client,salon,single=True);evidence(db,salon,visit_id,kind)
    before=base.snapshot(db,visit_id)
    body={'price':0,'note':'must rollback'} if change=='price' else {'items':[{'service_id':salon['second']['id']}],'note':'must rollback'}
    response=client.put(f'/visits/{visit_id}',headers={**salon['headers'],'Accept-Language':'ru'},json=body)
    assert response.status_code==409,response.text
    code='visit_price_payment_pending' if kind=='pending' else 'visit_price_settlement_required'
    assert response.json()['error']['message']==_(code,'ru')
    assert base.snapshot(db,visit_id)==before


@pytest.mark.parametrize('locale',['en','ru','cz'])
def test_refusal_is_localized(client,db,salon,locale):
    visit_id,items=base.create_quote(client,salon,single=True);evidence(db,salon,visit_id,'paid')
    response=client.put(f'/visits/{visit_id}',headers={**salon['headers'],'Accept-Language':locale},json={'price':1})
    assert response.status_code==409,response.text
    assert response.json()['error']['message']==_('visit_price_settlement_required',locale)


def test_move_and_same_chain_payload_keep_settled_quote_despite_zero_input(client,db,salon):
    from datetime import timedelta
    visit_id,items=base.create_quote(client,salon);evidence(db,salon,visit_id,'paid')
    before=base.snapshot(db,visit_id)
    response=client.put(f'/visits/{visit_id}',headers=salon['headers'],json={
        'date_start':(salon['start']+timedelta(days=1)).isoformat(),'items':items,'price':0,'note':'allowed move'})
    assert response.status_code==200,response.text
    assert response.json()['price']==dict(before[0])['price']
    assert [i['price'] for i in response.json()['items']]==[float(dict(i)['price']) for i in before[1]]


def test_unsettled_single_price_can_change_and_complete_in_one_request(client,db,salon):
    visit_id,_items=base.create_quote(client,salon,single=True)
    response=client.put(f'/visits/{visit_id}',headers=salon['headers'],json={'price':20,'status':'completed','payment_method':'cash'})
    assert response.status_code==200,response.text
    db.expire_all();visit=db.get(Visit,visit_id)
    assert visit.price==20 and visit.manual_payment_recorded_at is not None


def test_busy_attempt_returns_retry_without_partial_edit(client,db,salon):
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from app.db.models.service_payment import ServicePaymentAttempt
    visit_id,_items=base.create_quote(client,salon,single=True)
    aid=add_attempt(db,{'business':salon['business']['id'],'visit':visit_id},'busy-attempt','failed');db.commit()
    before=base.snapshot(db,visit_id)
    with Session(db.get_bind()) as holder:
        holder.execute(select(ServicePaymentAttempt.id).where(ServicePaymentAttempt.id==aid).with_for_update())
        response=client.put(f'/visits/{visit_id}',headers={**salon['headers'],'Accept-Language':'ru'},json={'price':1,'note':'not saved'})
        assert response.status_code==409,response.text
        assert response.json()['error']['message']==_('visit_price_retry','ru')
    assert base.snapshot(db,visit_id)==before


def test_real_deferred_commit_failure_rolls_back_price_marker_and_audit(client,db,salon):
    from fastapi.testclient import TestClient
    from sqlalchemy import text
    from app.main import app
    visit_id,_items=base.create_quote(client,salon,single=True);before=base.snapshot(db,visit_id)
    db.execute(text('''CREATE FUNCTION reject_price_commit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
        IF NEW.note='synthetic-deferred-price' THEN RAISE EXCEPTION 'synthetic price commit failure'; END IF;
        RETURN NEW; END $$'''))
    db.execute(text('''CREATE CONSTRAINT TRIGGER reject_price_commit AFTER UPDATE ON visits
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION reject_price_commit()'''));db.commit()
    try:
        with TestClient(app,raise_server_exceptions=False) as transport:
            response=transport.put(f'/visits/{visit_id}',headers=salon['headers'],json={
                'price':20,'status':'completed','payment_method':'cash','note':'synthetic-deferred-price'})
        assert response.status_code==500,response.text
        assert base.snapshot(db,visit_id)==before
    finally:
        db.rollback();db.execute(text('DROP TRIGGER reject_price_commit ON visits'))
        db.execute(text('DROP FUNCTION reject_price_commit()'));db.commit()


from tests import test_gifts as gifts
gift_world=gifts.gift_world


def test_real_gift_redemption_blocks_editor_even_after_reversal(client,db,gift_world):
    w=gift_world;order,paid=gifts._pay(client,w,request_key='editor-gift')
    result=client.post('/businesses/me/gift-certificates/redeem',headers=w['headers'],json={
        'code':paid['certificate']['code'],'visit_id':w['visit']['id'],'amount':10,'request_key':'editor-gift-redeem'})
    assert result.status_code==200,result.text
    reversed_result=client.post(f"/businesses/me/gift-certificates/{paid['certificate']['id']}/reversals",
        headers=w['headers'],json={'redemption_entry_id':result.json()['entry_id'],'request_key':'editor-gift-reverse'})
    assert reversed_result.status_code==200,reversed_result.text
    before=base.snapshot(db,w['visit']['id'])
    response=client.put(f"/visits/{w['visit']['id']}",headers={**w['headers'],'Accept-Language':'ru'},json={'price':0})
    assert response.status_code==409,response.text
    assert response.json()['error']['message']==_('visit_price_settlement_required','ru')
    assert base.snapshot(db,w['visit']['id'])==before
