"""Explicit replacement preserves scheduling and requires a current, unsettled quote."""
from decimal import Decimal

import pytest
from sqlalchemy import event,text,update
from sqlalchemy.orm import Session

from app.api.core import audit_service,visit_price_access,visit_price_quote
from app.api.core.visit_reprice import reprice_visit,QuoteChanged,QuoteItemsInvalid
from app.api.core.visit_settlement_guard import VisitPriceChangeBlocked,DirtySettlementState
from app.db.models.business_audit_event import BusinessAuditEvent
from app.db.models.client import Client
from app.db.models.visit import Visit
from app.schemas.visit_price import VisitPriceChange
from tests import test_visitor_confirm_core as base
from tests.test_visitor_pending_payments import add_attempt

world=base.world


def change(db,w,amount='40.00',currency='EUR'):
    visit=db.get(Visit,w['visit']);state=visit_price_access.read_stored_price(db,visit)
    payload=VisitPriceChange(currency=currency,expected_quote=visit_price_quote.fingerprint(state),
        items=[{'item_id':item.id,'price':amount} for item in state.items])
    return visit,payload


def test_explicit_price_and_unit_change_only_quote_fields_and_caller_commits(db,world):
    before=base.snapshot(db,world);db.rollback();visit,payload=change(db,world)
    result=reprice_visit(db,visit,payload)
    assert result.currency=='EUR' and result.total==Decimal('40.00')
    after=base.snapshot(db,world)
    assert {k for k in before[0] if before[0][k]!=after[0][k]}=={'price','currency_snapshot'}
    for old,new in zip(before[1],after[1]):
        assert {k:v for k,v in old.items() if k!='price'}=={k:v for k,v in new.items() if k!='price'}
    with Session(db.get_bind()) as observer:assert base.snapshot(observer,world)==before
    audit=db.query(BusinessAuditEvent).filter_by(action='visit.repriced').one()
    assert audit.changes['currency_snapshot']=={'old':None,'new':'EUR'}
    db.commit()
    visit,same=change(db,world);again=reprice_visit(db,visit,same);db.commit()
    assert again.expected_quote==result.expected_quote
    assert db.query(BusinessAuditEvent).filter_by(action='visit.repriced').count()==1


def test_whole_rollback_keeps_prior_quote_and_removes_audit(db,world):
    before=base.snapshot(db,world);db.rollback();visit,payload=change(db,world);reprice_visit(db,visit,payload);db.rollback()
    assert base.snapshot(db,world)==before
    assert db.query(BusinessAuditEvent).filter_by(action='visit.repriced').count()==0


def test_stale_quote_is_rejected_before_financial_or_audit_changes(db,world):
    visit,payload=change(db,world)
    db.execute(update(Visit).where(Visit.id==visit.id).values(price=123));db.commit()
    before=base.snapshot(db,world);db.rollback()
    with pytest.raises(QuoteChanged):reprice_visit(db,visit,payload)
    db.rollback();assert base.snapshot(db,world)==before


@pytest.mark.parametrize('kind',['paid','refunded','reconcile','pending','manual'])
def test_settlement_or_pending_attempt_blocks_explicit_unit_replacement(db,world,kind):
    if kind=='manual':db.execute(update(Visit).where(Visit.id==world['visit']).values(status='completed',payment_method='cash'))
    else:add_attempt(db,world,'explicit-quote-payment',kind)
    db.commit();before=base.snapshot(db,world);db.rollback();visit,payload=change(db,world)
    with pytest.raises(VisitPriceChangeBlocked) as exc:reprice_visit(db,visit,payload)
    assert exc.value.reason==('visit_price_payment_pending' if kind=='pending' else 'visit_price_settlement_required')
    db.rollback();assert base.snapshot(db,world)==before


def test_foreign_item_id_cannot_be_used_to_replace_the_stored_chain(db,world):
    visit,payload=change(db,world)
    bad=VisitPriceChange(currency='EUR',expected_quote=payload.expected_quote,items=[{'item_id':999999,'price':'40.00'}])
    with pytest.raises(QuoteItemsInvalid):reprice_visit(db,visit,bad)
    db.rollback()


def test_audit_failure_requires_whole_quote_rollback(db,world,monkeypatch):
    before=base.snapshot(db,world);db.rollback();visit,payload=change(db,world)
    monkeypatch.setattr(audit_service,'record_event',lambda *a,**k:None)
    with pytest.raises(VisitPriceChangeBlocked,match='visit_price_retry'):reprice_visit(db,visit,payload)
    db.rollback();assert base.snapshot(db,world)==before


def test_unrelated_pending_work_is_rejected_before_any_flush(db,world):
    visit,payload=change(db,world);pending=Client(full_name='Keep this edit');db.add(pending)
    with pytest.raises(DirtySettlementState):reprice_visit(db,visit,payload)
    assert pending.id is None and pending in db.new;db.rollback()


def test_actual_audit_insert_failure_cannot_leave_a_repriced_visit(db,world):
    before=base.snapshot(db,world);db.rollback()
    db.execute(text('''CREATE FUNCTION reject_reprice_audit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
        IF NEW.action='visit.repriced' THEN RAISE EXCEPTION 'synthetic audit failure'; END IF; RETURN NEW; END $$'''))
    db.execute(text('''CREATE TRIGGER reject_reprice_audit BEFORE INSERT ON business_audit_events
        FOR EACH ROW EXECUTE FUNCTION reject_reprice_audit()'''));db.commit()
    try:
        visit,payload=change(db,world)
        with pytest.raises(VisitPriceChangeBlocked,match='visit_price_retry'):reprice_visit(db,visit,payload)
        db.rollback();assert base.snapshot(db,world)==before
        assert db.query(BusinessAuditEvent).filter_by(action='visit.repriced').count()==0
    finally:
        db.rollback();db.execute(text('DROP TRIGGER reject_reprice_audit ON business_audit_events'))
        db.execute(text('DROP FUNCTION reject_reprice_audit()'));db.commit()


def test_actual_deferred_commit_failure_rolls_back_explicit_quote_and_audit(db,world):
    from sqlalchemy.exc import DBAPIError
    before=base.snapshot(db,world);db.rollback()
    db.execute(text('''CREATE FUNCTION reject_reprice_commit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
        IF NEW.currency_snapshot='EUR' THEN RAISE EXCEPTION 'synthetic deferred reprice failure'; END IF; RETURN NEW; END $$'''))
    db.execute(text('''CREATE CONSTRAINT TRIGGER reject_reprice_commit AFTER UPDATE ON visits
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION reject_reprice_commit()'''));db.commit()
    try:
        visit,payload=change(db,world);reprice_visit(db,visit,payload)
        with pytest.raises(DBAPIError):db.commit()
        db.rollback();assert base.snapshot(db,world)==before
        assert db.query(BusinessAuditEvent).filter_by(action='visit.repriced').count()==0
    finally:
        db.rollback();db.execute(text('DROP TRIGGER reject_reprice_commit ON visits'))
        db.execute(text('DROP FUNCTION reject_reprice_commit()'));db.commit()


def test_completed_free_visit_cannot_become_positive_without_payment_method(db,world):
    db.execute(update(Visit).where(Visit.id==world['visit']).values(status='completed',price=0,payment_method=None));db.commit()
    before=base.snapshot(db,world);db.rollback();visit,payload=change(db,world)
    with pytest.raises(VisitPriceChangeBlocked,match='payment_method_required_for_completed_visits'):reprice_visit(db,visit,payload)
    db.rollback();assert base.snapshot(db,world)==before
