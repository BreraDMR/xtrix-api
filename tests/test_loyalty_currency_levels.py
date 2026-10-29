"""Money thresholds use current-program currency without converting old visits."""
from datetime import date, datetime, timedelta
from decimal import Decimal, localcontext

import pytest
from sqlalchemy import event, update
from sqlalchemy.orm import Session

from app.api.core import loyalty_logic
from app.db.models.business import Business
from app.db.models.client_business_relation import ClientBusinessRelation
from app.db.models.client_cashback_balance import ClientCashbackBalance
from app.db.models.loyalty_program import LoyaltyLevel, LoyaltyProgram, LoyaltyPromotion, LoyaltyTransaction
from app.db.models.visit import Visit
from tests import test_loyalty_visit_quote_lock as base


@pytest.fixture
def world(db, monkeypatch):
    w=base.world.__wrapped__(db,monkeypatch)
    program=db.get(LoyaltyProgram,w['program']);program.program_type='cashback';program.use_levels=True
    level=LoyaltyLevel(program_id=program.id,name='Threshold level',level_number=1,min_spending=100,
        points_multiplier=2,cashback_multiplier=2)
    db.add(level);db.commit();w['level']=level.id
    return w


def history(db,w,price,currency='EUR',status='completed',deleted=False,client_id=None,business_id=None):
    count=db.query(Visit).count()
    start=datetime(2026,1,1)+timedelta(days=count)
    import math
    nonfinite=isinstance(price,float) and not math.isfinite(price)
    row=Visit(client_id=client_id or w['client'],business_id=business_id or w['business'],specialist_id=w['staff'],
        status=status,date_start=start,date_end=start+timedelta(minutes=30),price=1 if nonfinite else price,
        currency_snapshot=currency,payment_method='cash',deleted_at=datetime(2026,2,1) if deleted else None)
    db.add(row);db.commit()
    if nonfinite:
        db.execute(update(Visit).where(Visit.id==row.id).values(price=price));db.commit()
    return row


def test_old_currency_visit_does_not_qualify_a_current_money_promotion(db,world):
    w=world;visit=db.get(Visit,w['visit']);visit.currency_snapshot='CZK'
    program=db.get(LoyaltyProgram,w['program']);program.use_levels=False
    today=date.today();db.add(LoyaltyPromotion(program_id=program.id,name='EUR threshold',promotion_type='cashback',
        min_amount=100,cashback_percent=100,start_date=today,end_date=today+timedelta(days=1)))
    db.commit()
    _,cashback,promotion,_=loyalty_logic.calculate_bonus_for_visit(db,visit,program)
    assert promotion is None and cashback==Decimal('99.90')


def test_pure_percentage_promotion_can_apply_to_a_known_old_currency(db,world):
    w=world;visit=db.get(Visit,w['visit']);visit.currency_snapshot='CZK'
    program=db.get(LoyaltyProgram,w['program']);program.use_levels=False
    today=date.today();promo=LoyaltyPromotion(program_id=program.id,name='Pure percent',promotion_type='cashback',
        cashback_percent=100,start_date=today,end_date=today+timedelta(days=1));db.add(promo);db.commit()
    _,cashback,applied,_=loyalty_logic.calculate_bonus_for_visit(db,visit,program)
    assert applied.id==promo.id and cashback==Decimal('199.80')


def test_current_currency_money_promotion_still_applies(db,world):
    program=db.get(LoyaltyProgram,world['program']);program.use_levels=False
    today=date.today();promo=LoyaltyPromotion(program_id=program.id,name='Current threshold',promotion_type='cashback',
        min_amount=100,cashback_percent=100,start_date=today,end_date=today+timedelta(days=1));db.add(promo);db.commit()
    _,cashback,applied,_=loyalty_logic.calculate_bonus_for_visit(db,db.get(Visit,world['visit']),program)
    assert applied.id==promo.id and cashback==Decimal('199.80')


def test_stale_assigned_level_is_not_proof_of_current_currency_turnover(db,world):
    w=world;relation=db.query(ClientBusinessRelation).filter_by(client_id=w['client'],business_id=w['business']).one()
    relation.loyalty_level_id=w['level'];db.commit()
    history(db,w,10000,'CZK');history(db,w,10000,None)
    program=db.get(LoyaltyProgram,w['program'])
    _,cashback,_,level=loyalty_logic.calculate_bonus_for_visit(db,db.get(Visit,w['visit']),program)
    assert level is None and cashback==Decimal('99.90')


def test_turnover_uses_exact_current_currency_completed_live_visits_only(db,world):
    w=world;history(db,w,60.01);history(db,w,39.99)
    for price,unit,status,deleted in [(9999,'CZK','completed',False),(9999,None,'completed',False),
        (9999,'EUR','planned',False),(9999,'EUR','completed',True),(-1000,'EUR','completed',False),
        (float('nan'),'EUR','completed',False),(float('inf'),'EUR','completed',False)]:
        history(db,w,price,unit,status,deleted)
    db.get(LoyaltyLevel,w['level']).min_spending=100
    db.add(LoyaltyLevel(program_id=w['program'],name='Must not use foreign turnover',level_number=2,
        min_spending=1000,points_multiplier=9,cashback_multiplier=9));db.commit()
    with localcontext() as context:
        context.prec=2
        _,cashback,_,level=loyalty_logic.calculate_bonus_for_visit(db,db.get(Visit,w['visit']),db.get(LoyaltyProgram,w['program']))
    assert level is not None and level.id==w['level'] and cashback==Decimal('199.80')


def test_disabled_levels_and_zero_multiplier_are_respected(db,world):
    w=world;history(db,w,100)
    relation=db.query(ClientBusinessRelation).filter_by(client_id=w['client'],business_id=w['business']).one()
    relation.loyalty_level_id=w['level'];program=db.get(LoyaltyProgram,w['program']);program.use_levels=False;db.commit()
    _,cashback,_,level=loyalty_logic.calculate_bonus_for_visit(db,db.get(Visit,w['visit']),program)
    assert level is None and cashback==Decimal('99.90')
    program.use_levels=True;db.get(LoyaltyLevel,w['level']).cashback_multiplier=0;db.commit()
    _,cashback,_,level=loyalty_logic.calculate_bonus_for_visit(db,db.get(Visit,w['visit']),program)
    assert level.id==w['level'] and cashback==Decimal('0.00')


def test_award_level_and_wallet_commit_together_without_post_commit_level_call(db,world,monkeypatch):
    w=world;history(db,w,100)
    def forbidden(*args,**kwargs):raise AssertionError('Post-commit level write was called')
    monkeypatch.setattr(loyalty_logic,'update_client_level',forbidden)
    transaction=loyalty_logic.award_bonus_for_visit(db,db.get(Visit,w['visit']))
    assert transaction.level_id==w['level']
    with Session(db.get_bind()) as observer:
        assert observer.query(ClientBusinessRelation).filter_by(client_id=w['client'],business_id=w['business']).one().loyalty_level_id==w['level']
        assert observer.query(LoyaltyTransaction).filter_by(id=transaction.id).one().level_id==w['level']
        assert observer.query(ClientCashbackBalance).filter_by(client_id=w['client'],business_id=w['business'],currency='EUR').one().balance==Decimal('299.80')


def test_award_commit_failure_cannot_leave_a_changed_level_or_wallet(db,world):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    w=world;history(db,w,100)
    before=db.query(ClientCashbackBalance).filter_by(client_id=w['client'],business_id=w['business'],currency='EUR').one().balance
    db.execute(text('''CREATE FUNCTION reject_level_award() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
        IF NEW.transaction_type='earned' THEN RAISE EXCEPTION 'synthetic award rejection'; END IF; RETURN NEW; END $$'''))
    db.execute(text('''CREATE CONSTRAINT TRIGGER reject_level_award AFTER INSERT ON loyalty_transactions
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION reject_level_award()'''));db.commit()
    try:
        with pytest.raises(DBAPIError):loyalty_logic.award_bonus_for_visit(db,db.get(Visit,w['visit']))
        db.rollback()
        with Session(db.get_bind()) as observer:
            assert observer.query(ClientBusinessRelation).filter_by(client_id=w['client'],business_id=w['business']).one().loyalty_level_id is None
            assert observer.query(ClientCashbackBalance).filter_by(client_id=w['client'],business_id=w['business'],currency='EUR').one().balance==before
            assert observer.query(LoyaltyTransaction).filter_by(visit_id=w['visit']).count()==0
    finally:
        db.rollback();db.execute(text('DROP TRIGGER reject_level_award ON loyalty_transactions'));db.execute(text('DROP FUNCTION reject_level_award()'));db.commit()


def test_award_does_not_reuse_a_cached_program_after_disable(db,world):
    from sqlalchemy import update
    w=world;program=db.get(LoyaltyProgram,w['program']);assert program.is_active
    with Session(db.get_bind()) as worker:
        worker.execute(update(LoyaltyProgram).where(LoyaltyProgram.id==program.id).values(is_active=False));worker.commit()
    assert program.is_active
    assert loyalty_logic.award_bonus_for_visit(db,db.get(Visit,w['visit']),program) is None
    assert db.query(LoyaltyTransaction).filter_by(visit_id=w['visit']).count()==0


def test_standalone_level_update_uses_the_same_currency_scoped_turnover(db,world):
    w=world;history(db,w,100)
    level=loyalty_logic.update_client_level(db,w['client'],w['business'],db.get(LoyaltyProgram,w['program']))
    assert level.id==w['level']
    with Session(db.get_bind()) as observer:
        assert observer.query(ClientBusinessRelation).filter_by(client_id=w['client'],business_id=w['business']).one().loyalty_level_id==w['level']


def test_changed_money_promotion_does_not_reuse_a_cached_threshold(db,world):
    from sqlalchemy import update
    w=world;program=db.get(LoyaltyProgram,w['program']);program.use_levels=False
    today=date.today();promo=LoyaltyPromotion(program_id=program.id,name='Changed threshold',promotion_type='cashback',
        min_amount=100,cashback_percent=100,start_date=today,end_date=today+timedelta(days=1));db.add(promo);db.commit()
    assert promo.min_amount==100
    with Session(db.get_bind()) as worker:
        worker.execute(update(LoyaltyPromotion).where(LoyaltyPromotion.id==promo.id).values(min_amount=10000));worker.commit()
    assert promo.min_amount==100
    _,cashback,applied,_=loyalty_logic.calculate_bonus_for_visit(db,db.get(Visit,w['visit']),program)
    assert applied is None and cashback==Decimal('99.90')
