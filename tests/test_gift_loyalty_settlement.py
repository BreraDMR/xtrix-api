"""A gift and loyalty payment cannot independently pay the same unallocated quote."""
from decimal import Decimal

import pytest

from app.api.core import cashback_wallets, gift_service, loyalty_logic
from app.db.models.client_cashback_balance import ClientCashbackBalance
from app.db.models.gift import GiftLedgerEntry
from app.db.models.loyalty_program import LoyaltyTransaction
from app.db.models.visit import Visit
from tests import test_gifts as gifts

gift_world=gifts.gift_world


def prepare(client,db,w,kind='money'):
    _,paid=gifts._pay(client,w,offer_id=w[f'{kind}_offer']['id'],request_key='combined-purchase')
    certificate=gifts._certificate(db,paid);visit=db.get(Visit,w['visit']['id'])
    unit=certificate.currency if kind=='money' else visit.currency_snapshot
    visit.currency_snapshot=unit
    db.add(ClientCashbackBalance(client_id=visit.client_id,business_id=visit.business_id,currency=unit,balance=100))
    db.commit()
    return certificate.code,visit.id


def spend(db,w,visit_id):
    return loyalty_logic.use_points_for_visit(db,w['client']['id'],w['business']['id'],0,30,visit_id=visit_id)


@pytest.mark.parametrize('kind',['money','package'])
def test_new_gift_after_loyalty_payment_is_rejected_without_another_debit(client,db,gift_world,kind):
    w=gift_world;code,visit_id=prepare(client,db,w,kind)
    assert spend(db,w,visit_id)[0]
    with pytest.raises(gift_service.GiftError) as exc:
        gift_service.redeem(db,w['business']['id'],code,visit_id,None,None,'gift-after-loyalty',w['owner'].id)
    assert exc.value.reason=='loyalty_payment_already_recorded';db.rollback()
    assert db.query(GiftLedgerEntry).filter_by(visit_id=visit_id,entry_type='redeemed').count()==0
    assert db.query(ClientCashbackBalance).one().balance==70


def test_gift_first_prevents_a_later_cashback_debit(client,db,gift_world):
    w=gift_world;code,visit_id=prepare(client,db,w)
    gift_service.redeem(db,w['business']['id'],code,visit_id,20,None,'gift-first',w['owner'].id)
    with pytest.raises(cashback_wallets.CashbackError,match='loyalty_payment_settlement_required'):
        spend(db,w,visit_id)
    db.rollback()
    assert db.query(ClientCashbackBalance).one().balance==100
    assert db.query(LoyaltyTransaction).filter_by(visit_id=visit_id).count()==0


def test_historical_gift_receipt_still_replays_after_a_legacy_loyalty_payment(client,db,gift_world):
    w=gift_world;code,visit_id=prepare(client,db,w)
    _,first,amount,units=gift_service.redeem(db,w['business']['id'],code,visit_id,20,None,'historical-gift',w['owner'].id)
    entry_id=first.id
    # Old mixed receipts remain immutable evidence; this is not a new supported payment.
    db.add(LoyaltyTransaction(client_id=w['client']['id'],business_id=w['business']['id'],visit_id=visit_id,
        transaction_type='spent',points_amount=-1,points_balance_after=0,cashback_amount=0,cashback_balance_after=0))
    db.commit()
    _,again,replayed,replayed_units=gift_service.redeem(db,w['business']['id'],code,visit_id,20,None,'historical-gift',w['owner'].id)
    assert again.id==entry_id and replayed==amount==Decimal('20.00') and replayed_units==units==0
    with pytest.raises(gift_service.GiftError) as exc:
        gift_service.redeem(db,w['business']['id'],code,visit_id,1,None,'new-gift',w['owner'].id)
    assert exc.value.reason=='loyalty_payment_already_recorded';db.rollback()
    assert db.query(GiftLedgerEntry).filter_by(visit_id=visit_id,entry_type='redeemed').count()==1


@pytest.mark.parametrize('first_kind',['gift','loyalty'])
def test_visit_lock_serializes_racing_gift_and_loyalty_payments(client,db,gift_world,first_kind):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from sqlalchemy import event,text
    from sqlalchemy.orm import Session
    from tests.test_visitor_action_target import wait_blocked
    w=gift_world;code,visit_id=prepare(client,db,w)
    engine=db.get_bind();first_ready=Event();release=Event();second_ready=Event();pids={}
    def pay(kind,first):
        with Session(engine) as worker:
            if first:
                def pause(session):
                    first_ready.set();assert release.wait(5)
                event.listen(worker,'before_commit',pause)
            else:
                pids['second']=worker.execute(text('SELECT pg_backend_pid()')).scalar_one();second_ready.set()
            try:
                if kind=='gift':
                    gift_service.redeem(worker,w['business']['id'],code,visit_id,20,None,'race-gift',w['owner'].id)
                else:
                    assert spend(worker,w,visit_id)[0]
                return True,None
            except (gift_service.GiftError,cashback_wallets.CashbackError) as exc:
                worker.rollback();return False,exc.reason
    second_kind='loyalty' if first_kind=='gift' else 'gift'
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(pay,first_kind,True)
        try:
            assert first_ready.wait(5)
            second=pool.submit(pay,second_kind,False)
            assert second_ready.wait(5);assert wait_blocked(engine,pids['second'])
        finally:release.set()
        assert first.result(timeout=5)==(True,None)
        ok,reason=second.result(timeout=5)
        assert not ok and reason==('loyalty_payment_settlement_required' if first_kind=='gift' else 'loyalty_payment_already_recorded')
    db.expire_all()
    assert db.query(ClientCashbackBalance).one().balance==(100 if first_kind=='gift' else 70)
    assert db.query(GiftLedgerEntry).filter_by(visit_id=visit_id,entry_type='redeemed').count()==(1 if first_kind=='gift' else 0)
    assert db.query(LoyaltyTransaction).filter_by(visit_id=visit_id,transaction_type='spent').count()==(0 if first_kind=='gift' else 1)


@pytest.mark.parametrize('payment',['pending','paid','failed','manual'])
def test_gift_cannot_pay_on_top_of_an_external_or_recorded_manual_payment(client,db,gift_world,payment):
    from tests.test_visitor_pending_payments import add_attempt
    w=gift_world;code,visit_id=prepare(client,db,w)
    if payment=='manual':
        visit=db.get(Visit,visit_id);visit.status='completed';visit.payment_method='cash'
    else:
        add_attempt(db,{'business':w['business']['id'],'visit':visit_id},'gift-existing-payment',payment)
    db.commit()
    with pytest.raises(gift_service.GiftError) as exc:
        gift_service.redeem(db,w['business']['id'],code,visit_id,20,None,'gift-existing-payment',w['owner'].id)
    assert exc.value.reason==('loyalty_payment_settlement_required' if payment=='manual' else 'loyalty_payment_checkout_exists')
    db.rollback();assert db.query(GiftLedgerEntry).filter_by(visit_id=visit_id,entry_type='redeemed').count()==0


def test_gift_does_not_wait_behind_an_attempt_first_payment_writer(client,db,gift_world):
    from tests.test_visitor_pending_payments import add_attempt
    from app.db.models.service_payment import ServicePaymentAttempt
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    w=gift_world;code,visit_id=prepare(client,db,w)
    attempt=add_attempt(db,{'business':w['business']['id'],'visit':visit_id},'gift-busy-attempt','failed');db.commit()
    with Session(db.get_bind()) as holder:
        holder.execute(select(ServicePaymentAttempt.id).where(ServicePaymentAttempt.id==attempt).with_for_update())
        with pytest.raises(gift_service.GiftError) as exc:
            gift_service.redeem(db,w['business']['id'],code,visit_id,20,None,'gift-busy-attempt',w['owner'].id)
        assert exc.value.reason=='visit_price_retry';db.rollback()
        with Session(db.get_bind()) as observer:
            observer.execute(select(Visit.id).where(Visit.id==visit_id).with_for_update(nowait=True))
            assert observer.query(GiftLedgerEntry).filter_by(visit_id=visit_id,entry_type='redeemed').count()==0


@pytest.mark.parametrize('locale',['en','ru','cz'])
def test_gift_api_explains_a_conflicting_loyalty_payment(client,db,gift_world,locale):
    from app.api.core.i18n import _
    w=gift_world;code,visit_id=prepare(client,db,w);assert spend(db,w,visit_id)[0]
    response=client.post('/businesses/me/gift-certificates/redeem',
        headers={**w['headers'],'Accept-Language':locale},
        json={'code':code,'visit_id':visit_id,'amount':20,'request_key':'explain-conflict'})
    assert response.status_code==409,response.text
    assert response.json()['error']['message']==_('loyalty_payment_already_recorded',locale)
    assert db.query(GiftLedgerEntry).filter_by(visit_id=visit_id,entry_type='redeemed').count()==0
