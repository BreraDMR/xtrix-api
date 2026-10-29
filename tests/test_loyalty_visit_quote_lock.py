"""Loyalty reads a locked current visit quote before calculating financial amounts."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import select,text,update
from sqlalchemy.orm import Session

from app.api.core import loyalty_logic
from app.api.core.visit_settlement_guard import settlement_reason
from app.api.crud import crud_loyalty
from app.db.models.client_business_relation import ClientBusinessRelation
from app.db.models.loyalty_program import LoyaltyProgram,LoyaltyTransaction
from app.db.models.visit import Visit
from app.db.models.business import Business
from app.db.models.client_cashback_balance import ClientCashbackBalance
from tests import test_visitor_confirm_core as core
from tests import test_visitor_action_target as locks


@pytest.fixture
def world(db,monkeypatch):
    w=core.world.__wrapped__(db,monkeypatch)
    program=LoyaltyProgram(business_id=w['business'],program_type='hybrid',points_per_currency=1,
        cashback_percent=10,currency_per_point=1,max_bonus_payment_percent=50,is_active=True)
    db.add(program);db.add(ClientBusinessRelation(client_id=w['client'],business_id=w['business'],
        loyalty_points=100,loyalty_cashback=0));
    db.get(Business,w['business']).currency='EUR';db.get(Visit,w['visit']).currency_snapshot='EUR'
    db.add(ClientCashbackBalance(client_id=w['client'],business_id=w['business'],currency='EUR',balance=100))
    db.commit();w['program']=program.id
    return w


def test_award_refreshes_a_cached_visit_price_before_calculation(db,world):
    visit=db.get(Visit,world['visit']);assert visit.price==999
    with Session(db.get_bind()) as writer:
        writer.execute(update(Visit).where(Visit.id==visit.id).values(price=20));writer.commit()
    assert visit.price==999
    awarded=loyalty_logic.award_bonus_for_visit(db,visit)
    assert awarded.points_amount==20 and awarded.cashback_amount==2


def test_cashback_only_spending_is_one_real_transaction_using_current_visit_cap(db,world):
    visit=db.get(Visit,world['visit'])
    with Session(db.get_bind()) as writer:
        writer.execute(update(Visit).where(Visit.id==visit.id).values(price=20));writer.commit()
    success,discount,error=loyalty_logic.use_points_for_visit(db,world['client'],world['business'],0,100,
        visit_id=visit.id,visit_amount=999)
    assert success and discount==10,error
    tx=db.query(LoyaltyTransaction).filter_by(visit_id=visit.id,transaction_type='spent').one()
    assert tx.points_amount==0 and tx.cashback_amount==-10
    assert settlement_reason(db,visit.id,world['business'])=='visit_price_settlement_required';db.rollback()


def test_award_waits_for_price_writer_then_uses_committed_quote(db,world):
    engine=db.get_bind();ready=Event();holder={}
    db.execute(select(Visit.id).where(Visit.id==world['visit']).with_for_update())
    def award():
        with Session(engine) as worker:
            visit=worker.get(Visit,world['visit']);holder['pid']=worker.execute(text('SELECT pg_backend_pid()')).scalar_one();ready.set()
            tx=loyalty_logic.award_bonus_for_visit(worker,visit);return tx.points_amount
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(award)
        try:
            assert ready.wait(5);assert locks.wait_blocked(engine,holder['pid'])
            db.execute(update(Visit).where(Visit.id==world['visit']).values(price=30));db.commit()
        finally:db.rollback()
        assert future.result(timeout=5)==30


def test_generic_visit_linked_transaction_waits_before_writing_balance(db,world):
    engine=db.get_bind();ready=Event();holder={}
    db.execute(select(Visit.id).where(Visit.id==world['visit']).with_for_update())
    def record():
        with Session(engine) as worker:
            holder['pid']=worker.execute(text('SELECT pg_backend_pid()')).scalar_one();ready.set()
            crud_loyalty.create_loyalty_transaction(worker,world['client'],world['business'],'earned',1,visit_id=world['visit'])
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(record)
        try:
            assert ready.wait(5);assert locks.wait_blocked(engine,holder['pid'])
            with Session(engine) as observer:assert observer.query(ClientBusinessRelation).one().loyalty_points==100
            db.rollback()
        finally:db.rollback()
        future.result(timeout=5)


def test_points_and_cashback_spend_are_one_atomic_ledger_entry(db,world):
    success,discount,error=loyalty_logic.use_points_for_visit(db,world['client'],world['business'],5,5,
        visit_id=world['visit'],visit_amount=999)
    assert success and discount==10,error
    tx=db.query(LoyaltyTransaction).filter_by(visit_id=world['visit'],transaction_type='spent').one()
    assert tx.points_amount==-5 and tx.cashback_amount==-5


def test_fresh_locked_balance_prevents_spending_a_cached_old_balance(db,world):
    relation=db.query(ClientBusinessRelation).one();assert relation.loyalty_points==100
    with Session(db.get_bind()) as writer:
        writer.execute(update(ClientBusinessRelation).where(ClientBusinessRelation.id==relation.id).values(loyalty_points=1));writer.commit()
    assert relation.loyalty_points==100
    with pytest.raises(ValueError,match='loyalty_balance_insufficient'):
        crud_loyalty.create_loyalty_transaction(db,world['client'],world['business'],'spent',-5,visit_id=world['visit'])
    db.rollback()
    assert db.query(ClientBusinessRelation).one().loyalty_points==1
    assert db.query(LoyaltyTransaction).count()==0


def test_unrelated_client_cannot_receive_a_visit_linked_bonus(db,world):
    with pytest.raises(ValueError,match='visit_not_found'):
        crud_loyalty.create_loyalty_transaction(db,world['other_client'],world['business'],'earned',1,visit_id=world['visit'])
    db.rollback();assert db.query(LoyaltyTransaction).count()==0


def test_repeated_visit_award_returns_original_record_without_double_credit(db,world):
    visit=db.get(Visit,world['visit']);first=loyalty_logic.award_bonus_for_visit(db,visit)
    original=first.id;before=db.query(ClientBusinessRelation).one().loyalty_points
    again=loyalty_logic.award_bonus_for_visit(db,visit)
    assert again.id==original
    assert db.query(LoyaltyTransaction).filter_by(visit_id=visit.id,transaction_type='earned').count()==1
    assert db.query(ClientBusinessRelation).one().loyalty_points==before
