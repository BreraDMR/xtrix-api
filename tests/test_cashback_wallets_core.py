"""Known units never consume the legacy amount or another currency's balance."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal,localcontext
from threading import Event

import pytest
from sqlalchemy import select,text,update
from sqlalchemy.orm import Session

from app.api.core.cashback_wallets import balances,known_balance,move_balance,CashbackError
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.client_business_relation import ClientBusinessRelation
from app.db.models.client_cashback_balance import ClientCashbackBalance
from tests import test_visitor_confirm_core as core
from tests import test_visitor_action_target as locks


@pytest.fixture
def world(db,monkeypatch):
    w=core.world.__wrapped__(db,monkeypatch)
    db.add(ClientBusinessRelation(client_id=w['client'],business_id=w['business'],loyalty_points=0,loyalty_cashback=123.456789))
    db.commit();return w


def move(db,w,currency,amount):return move_balance(db,w['client'],w['business'],currency,amount)


def test_cent_math_is_independent_of_callers_decimal_context(db,world):
    db.add(ClientCashbackBalance(client_id=world['client'],business_id=world['business'],currency='EUR',balance=Decimal('1000.01')));db.commit()
    with localcontext() as ctx:
        ctx.prec=2
        moved=move(db,world,'EUR',Decimal('0.01'))
    assert moved.balance_after==Decimal('1000.02')
    db.rollback()


def test_separate_currencies_and_unknown_balance_preserve_exact_history(db,world):
    move(db,world,'CZK',Decimal('100.01'));move(db,world,'EUR',Decimal('2.99'))
    with Session(db.get_bind()) as observer:
        assert known_balance(observer,world['client'],world['business'],'CZK')==0
    db.commit()
    assert known_balance(db,world['client'],world['business'],'EUR')==Decimal('2.99')
    rows=balances(db,world['client'],world['business'])
    assert [(r.currency,r.amount,r.requires_review) for r in rows]==[
        ('CZK',Decimal('100.01'),False),('EUR',Decimal('2.99'),False),(None,Decimal('123.456789'),True)]
    assert db.query(ClientBusinessRelation).one().loyalty_cashback==123.456789


def test_czk_or_unknown_money_cannot_pay_a_euro_debit(db,world):
    move(db,world,'CZK',100);db.commit()
    with pytest.raises(CashbackError,match='loyalty_balance_insufficient'):move(db,world,'EUR',-1)
    db.rollback();assert known_balance(db,world['client'],world['business'],'CZK')==100
    assert db.query(ClientBusinessRelation).one().loyalty_cashback==123.456789


def test_caller_rollback_restores_wallet_amount(db,world):
    move(db,world,'EUR',5);db.commit();move(db,world,'EUR',-2);db.rollback()
    assert known_balance(db,world['client'],world['business'],'EUR')==5


@pytest.mark.parametrize('unit,amount',[(None,1),('ZZZ',1),('eur',1),('EUR',True),('EUR','NaN'),('EUR','0.001')])
def test_invalid_unit_or_amount_never_creates_a_wallet(db,world,unit,amount):
    with pytest.raises(CashbackError):move(db,world,unit,amount)
    db.rollback();assert db.query(ClientCashbackBalance).count()==0


def test_two_debits_cannot_spend_the_same_money(db,world):
    move(db,world,'EUR',10);db.commit();assert move(db,world,'EUR',-8).balance_after==2
    engine=db.get_bind();ready=Event();holder={}
    def spend():
        with Session(engine) as worker:
            holder['pid']=worker.execute(text('SELECT pg_backend_pid()')).scalar_one();ready.set()
            try:move(worker,world,'EUR',-8)
            except CashbackError as exc:worker.rollback();return exc.reason
            worker.commit();return None
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(spend)
        try:
            assert ready.wait(5);assert locks.wait_blocked(engine,holder['pid']);db.commit()
        finally:db.rollback()
        assert future.result(timeout=5)=='loyalty_balance_insufficient'
    assert known_balance(db,world['client'],world['business'],'EUR')==2


def test_known_wallet_read_does_not_flush_pending_client(db,world):
    pending=Client(full_name='Do not flush');db.add(pending)
    assert known_balance(db,world['client'],world['business'],'EUR')==0
    assert pending.id is None;db.rollback()


def test_pending_wallet_edit_is_rejected_without_losing_it(db,world):
    move(db,world,'EUR',5);db.commit();wallet=db.query(ClientCashbackBalance).one();wallet.balance=Decimal('7.00')
    with pytest.raises(CashbackError,match='visit_price_retry'):move(db,world,'EUR',1)
    assert wallet.balance==7 and wallet in db.dirty;db.rollback()


def test_missing_customer_relation_cannot_create_a_currency_balance(db,world):
    with pytest.raises(CashbackError,match='client_not_found'):
        move_balance(db,world['other_client'],world['business'],'EUR',10)
    db.rollback();assert db.query(ClientCashbackBalance).count()==0
