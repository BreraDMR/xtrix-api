"""Known-unit ledger writes never consume or re-label historical untyped cash."""
from decimal import Decimal

import pytest
from sqlalchemy import event, update
from sqlalchemy.orm import Session

from app.api.core import cashback_wallets, loyalty_logic
from app.api.crud import crud_loyalty
from app.db.models.business import Business
from app.db.models.client_business_relation import ClientBusinessRelation
from app.db.models.client_cashback_balance import ClientCashbackBalance
from app.db.models.loyalty_program import LoyaltyProgram, LoyaltyTransaction
from app.db.models.visit import Visit
from tests import test_loyalty_visit_quote_lock as quote


@pytest.fixture
def world(db,monkeypatch):
    return quote.world.__wrapped__(db,monkeypatch)


def test_award_uses_the_visit_unit_even_after_the_business_switches_currency(db, world):
    visit=db.get(Visit,world['visit']);visit.currency_snapshot='CZK';visit.price=100.10
    db.get(LoyaltyProgram,world['program']).program_type='cashback'
    relation=db.query(ClientBusinessRelation).one();relation.loyalty_cashback=123.456789
    db.commit()
    tx=loyalty_logic.award_bonus_for_visit(db,visit)
    assert tx.cashback_currency=='CZK'
    assert tx.cashback_amount_exact==Decimal('10.01') and tx.cashback_balance_after_exact==Decimal('10.01')
    assert cashback_wallets.known_balance(db,world['client'],world['business'],'EUR')==100
    assert cashback_wallets.known_balance(db,world['client'],world['business'],'CZK')==Decimal('10.01')
    assert db.query(ClientBusinessRelation).one().loyalty_cashback==123.456789


def test_legacy_cash_and_another_currency_cannot_pay_a_euro_visit(db,world):
    db.query(ClientCashbackBalance).delete()
    db.query(ClientBusinessRelation).one().loyalty_cashback=500
    db.add(ClientCashbackBalance(client_id=world['client'],business_id=world['business'],currency='CZK',balance=500))
    db.commit()
    result=loyalty_logic.use_points_for_visit(db,world['client'],world['business'],0,10,visit_id=world['visit'])
    assert result==(False,0.0,'loyalty_balance_insufficient')
    assert db.query(LoyaltyTransaction).count()==0
    assert db.query(ClientBusinessRelation).one().loyalty_cashback==500
    assert db.query(ClientCashbackBalance).one().balance==500


def test_points_conversion_does_not_apply_current_euro_rates_to_a_czk_visit(db,world):
    db.get(Visit,world['visit']).currency_snapshot='CZK';db.commit()
    with pytest.raises(cashback_wallets.CashbackError,match='loyalty_points_currency_unavailable'):
        loyalty_logic.use_points_for_visit(db,world['client'],world['business'],10,0,visit_id=world['visit'])
    db.rollback();assert db.query(LoyaltyTransaction).count()==0


@pytest.mark.parametrize('price,percent',[(100,0),(0,50),(None,50)])
def test_zero_or_unknown_quote_never_allows_a_cashback_debit(db,world,price,percent):
    db.get(Visit,world['visit']).price=price
    db.get(LoyaltyProgram,world['program']).max_bonus_payment_percent=percent;db.commit()
    success,discount,error=loyalty_logic.use_points_for_visit(db,world['client'],world['business'],0,10,visit_id=world['visit'])
    assert not success and discount==0
    assert db.query(LoyaltyTransaction).count()==0
    assert db.query(ClientCashbackBalance).one().balance==100


def test_fractional_cap_rounds_down_to_cents_and_does_not_overspend(db,world):
    db.get(Visit,world['visit']).price=40
    db.get(LoyaltyProgram,world['program']).max_bonus_payment_percent=33.33;db.commit()
    success,discount,error=loyalty_logic.use_points_for_visit(db,world['client'],world['business'],0,20,visit_id=world['visit'])
    assert success and Decimal(str(discount))==Decimal('13.33'),error
    tx=db.query(LoyaltyTransaction).one()
    assert tx.cashback_amount_exact==Decimal('-13.33')
    assert tx.cashback_balance_after_exact==Decimal('86.67')


def test_cashback_consuming_the_cap_does_not_leave_negative_points(db,world):
    db.get(Visit,world['visit']).price=20;db.commit()
    success,discount,error=loyalty_logic.use_points_for_visit(db,world['client'],world['business'],5,20,visit_id=world['visit'])
    assert success and discount==10,error
    tx=db.query(LoyaltyTransaction).one()
    assert tx.points_amount==0 and tx.cashback_amount_exact==Decimal('-10.00')
    assert db.query(ClientBusinessRelation).one().loyalty_points==100


def test_commit_failure_rolls_back_wallet_points_and_exact_ledger_together(db,world):
    engine=db.get_bind()
    def reject(session):
        session.flush()
        raise RuntimeError('simulated commit failure')
    event.listen(db,'before_commit',reject)
    try:
        with pytest.raises(RuntimeError,match='simulated commit failure'):
            crud_loyalty.create_loyalty_transaction(db,world['client'],world['business'],'spent',-5,
                cashback_amount=-5,visit_id=world['visit'])
    finally:event.remove(db,'before_commit',reject)
    with Session(engine) as observer:
        assert observer.query(ClientCashbackBalance).one().balance==100
        assert observer.query(ClientBusinessRelation).one().loyalty_points==100
        assert observer.query(LoyaltyTransaction).count()==0


def test_free_unknown_visit_has_no_bonus_and_needs_no_currency_conversion(db,world):
    visit=db.get(Visit,world['visit']);visit.price=0;visit.currency_snapshot=None;db.commit()
    assert loyalty_logic.award_bonus_for_visit(db,visit) is None
    assert db.query(LoyaltyTransaction).count()==0


def test_repeated_spend_cannot_bypass_the_visit_cap(db,world):
    db.get(Visit,world['visit']).price=100;db.commit()
    first=loyalty_logic.use_points_for_visit(db,world['client'],world['business'],0,50,visit_id=world['visit'])
    assert first[0] and first[1]==50
    second=loyalty_logic.use_points_for_visit(db,world['client'],world['business'],0,50,visit_id=world['visit'])
    assert second==(False,0.0,'loyalty_payment_already_recorded')
    assert db.query(LoyaltyTransaction).count()==1
    assert db.query(ClientCashbackBalance).one().balance==50


def test_cashback_cap_uses_the_fresh_program_instead_of_a_cached_old_limit(db,world):
    program=db.get(LoyaltyProgram,world['program']);assert program.max_bonus_payment_percent==50
    with Session(db.get_bind()) as writer:
        writer.execute(update(LoyaltyProgram).where(LoyaltyProgram.id==program.id).values(max_bonus_payment_percent=0));writer.commit()
    assert program.max_bonus_payment_percent==50
    success,discount,error=loyalty_logic.use_points_for_visit(db,world['client'],world['business'],0,10,
        visit_id=world['visit'],program=program)
    assert not success and discount==0
    assert db.query(LoyaltyTransaction).count()==0


def test_two_first_awards_create_one_relation_and_keep_both_credits(db,world):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from datetime import timedelta
    engine=db.get_bind();original=db.get(Visit,world['visit'])
    second=Visit(business_id=world['business'],client_id=world['client'],specialist_id=world['staff'],
        status='planned',date_start=original.date_start+timedelta(days=2),
        date_end=original.date_end+timedelta(days=2),price=10,currency_snapshot='EUR')
    db.add(second);db.flush();visit_ids=[original.id,second.id]
    db.query(ClientCashbackBalance).delete();db.query(ClientBusinessRelation).delete();db.commit()
    barrier=Barrier(2)
    def pause_after_missing_relation(conn,cursor,statement,*args):
        if ('FROM client_business_relations' in statement and 'FOR UPDATE' in statement
                and cursor.rowcount==0):
            barrier.wait(timeout=5)
    def award(visit_id):
        with Session(engine) as worker:
            return crud_loyalty.create_loyalty_transaction(worker,world['client'],world['business'],
                'earned',1,cashback_amount=1,visit_id=visit_id).id
    event.listen(engine,'after_cursor_execute',pause_after_missing_relation)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(award,visit_ids))
    finally:event.remove(engine,'after_cursor_execute',pause_after_missing_relation)
    assert len(set(results))==2
    assert db.query(ClientBusinessRelation).one().loyalty_points==2
    assert db.query(ClientCashbackBalance).one().balance==Decimal('2.00')
    assert db.query(LoyaltyTransaction).count()==2
