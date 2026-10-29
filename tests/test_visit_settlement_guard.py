"""Historical payment evidence cannot be netted away or hidden by current status."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event

import pytest
from sqlalchemy import event, select, text, update
from sqlalchemy.orm import Session

from app.api.core.visit_settlement_guard import (
    settlement_reason, SettlementBusy, DirtySettlementState, SettlementTargetUnavailable,
)
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.gift import GiftLedgerEntry
from app.db.models.loyalty_program import LoyaltyTransaction
from app.db.models.service_payment import ServicePaymentAttempt, ServicePaymentEvent, ServicePaymentRefund
from app.db.models.visit import Visit
from tests import test_visitor_confirm_core as core
from tests import test_visitor_action_target as locks
from tests.test_visitor_pending_payments import add_attempt

world=core.world
SETTLED='visit_price_settlement_required'
PENDING='visit_price_payment_pending'


def reason(db,w):return settlement_reason(db,w['visit'],w['business'])


def test_unsettled_read_is_select_only_and_does_not_flush_unrelated_pending(db,world):
    unrelated=Client(full_name='Not part of the quote');db.add(unrelated);writes=[]
    def capture(conn,cursor,sql,*args):
        if sql.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')):writes.append(sql)
    event.listen(db.get_bind(),'before_cursor_execute',capture)
    try:assert reason(db,world) is None
    finally:event.remove(db.get_bind(),'before_cursor_execute',capture)
    assert not writes and unrelated.id is None;db.rollback()


@pytest.mark.parametrize('status,expected',[(s,SETTLED) for s in ('paid','partially_refunded','refunded','reconcile')]+
    [('pending',PENDING),('failed',None),('cancelled',None),('expired',None)])
def test_attempt_state_and_late_reconcile_are_history_even_without_paid_at(db,world,status,expected):
    add_attempt(db,world,'status-probe',status);db.commit()
    assert db.query(ServicePaymentAttempt).one().paid_at is None
    assert reason(db,world)==expected;db.rollback()


@pytest.mark.parametrize('kind',['paid_at','refund_amount','paid_event','refund_event','refund_row'])
def test_closed_attempt_cannot_hide_recorded_payment_or_refund(db,world,kind):
    attempt_id=add_attempt(db,world,'closed-evidence','cancelled');db.commit()
    if kind=='paid_at':db.get(ServicePaymentAttempt,attempt_id).paid_at=locks.NOW
    elif kind=='refund_amount':db.get(ServicePaymentAttempt,attempt_id).refunded_amount=1
    elif kind in ('paid_event','refund_event'):
        db.add(ServicePaymentEvent(attempt_id=attempt_id,business_id=world['business'],provider='synthetic',
            provider_event_id='historical-event',event_type='payment.paid' if kind=='paid_event' else 'refund.succeeded',
            status='paid',payload_hash='a'*64))
    else:db.add(ServicePaymentRefund(attempt_id=attempt_id,business_id=world['business'],provider='synthetic',
        refund_key='fully-refunded',amount=12.30,currency='CZK',status='succeeded'))
    db.commit();assert reason(db,world)==SETTLED;db.rollback()


@pytest.mark.parametrize('kind,points,cashback,expected',[
    ('earned',2,0,SETTLED),('earned',0,2,SETTLED),('spent',-2,0,SETTLED),('spent',0,-2,SETTLED),
    ('earned',-2,0,SETTLED),('level_up',0,0,None),('earned',0,0,None)])
def test_linked_points_and_cashback_are_history_not_current_balance(db,world,kind,points,cashback,expected):
    db.add(LoyaltyTransaction(client_id=world['client'],business_id=world['business'],visit_id=world['visit'],
        transaction_type=kind,points_amount=points,cashback_amount=cashback,points_balance_after=0,cashback_balance_after=0))
    db.commit();assert reason(db,world)==expected;db.rollback()


def test_manual_payment_remains_settled_after_reopen_and_method_clear(db,world):
    db.execute(update(Visit).where(Visit.id==world['visit']).values(status='completed',payment_method='cash'));db.commit()
    db.execute(update(Visit).where(Visit.id==world['visit']).values(status='planned',payment_method=None));db.commit()
    assert reason(db,world)==SETTLED;db.rollback()


def test_settlement_has_priority_over_pending_attempt(db,world):
    add_attempt(db,world,'pending');add_attempt(db,world,'paid','paid');db.commit()
    assert reason(db,world)==SETTLED;db.rollback()


@pytest.mark.parametrize('status',['pending','failed','expired','cancelled'])
def test_attempt_first_writer_never_forms_a_visit_wait_cycle(db,world,status):
    aid=add_attempt(db,world,'locked-attempt',status);db.commit();engine=db.get_bind();ready=Event();release=Event();holder={}
    def writer():
        with Session(engine) as worker:
            worker.execute(select(ServicePaymentAttempt.id).where(ServicePaymentAttempt.id==aid).with_for_update())
            holder['pid']=worker.execute(text('SELECT pg_backend_pid()')).scalar_one();ready.set();release.wait(5)
            worker.execute(update(Visit).where(Visit.id==world['visit']).values(note='Payment writer finished'));worker.commit()
    db.execute(select(Visit.id).where(Visit.id==world['visit']).with_for_update())
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(writer)
        try:
            assert ready.wait(5);release.set();assert locks.wait_blocked(engine,holder['pid'])
            with pytest.raises(SettlementBusy):reason(db,world)
            db.rollback();release.set()
        finally:release.set();db.rollback()
        future.result(timeout=5)
    assert db.get(Visit,world['visit']).note=='Payment writer finished'


def test_foreign_child_business_is_not_silently_filtered_away(db,world):
    other=Business(name='Other synthetic business',business_type='salon');db.add(other);db.flush()
    add_attempt(db,world,'foreign-attempt','failed',business_id=other.id);db.commit()
    assert reason(db,world)==SETTLED;db.rollback()


def test_dirty_financial_objects_are_not_flushed_or_refreshed_away(db,world):
    aid=add_attempt(db,world,'pending');db.commit();attempt=db.get(ServicePaymentAttempt,aid);attempt.status='paid'
    with pytest.raises(DirtySettlementState):reason(db,world)
    assert attempt in db.dirty and attempt.status=='paid';db.rollback()


def test_foreign_or_deleted_target_is_unavailable(db,world):
    with pytest.raises(SettlementTargetUnavailable):settlement_reason(db,world['visit'],world['business']+100)
    db.rollback()


def test_gift_reversal_never_removes_historical_redemption(db):
    from tests.test_visit_currency_snapshot import financial_world
    w=financial_world.__wrapped__(db)
    original=db.query(GiftLedgerEntry).filter_by(visit_id=w['visit'],entry_type='redeemed').one()
    db.add(GiftLedgerEntry(certificate_id=original.certificate_id,business_id=w['business'],entry_type='reversed',
        amount_delta=-original.amount_delta,units_delta=0,request_key='reversed-to-zero',visit_id=w['visit']))
    # Remove the independent payment proof so this tests gift history alone.
    db.query(ServicePaymentRefund).delete();db.query(ServicePaymentAttempt).delete();db.commit()
    assert settlement_reason(db,w['visit'],w['business'])==SETTLED;db.rollback()


def test_update_target_refreshes_cached_price_without_flushing_unrelated_object(db,world):
    from app.api.core.visit_settlement_guard import lock_quote_update_target
    visit=db.get(Visit,world['visit']);assert visit.price==999
    with Session(db.get_bind()) as writer:
        writer.execute(update(Visit).where(Visit.id==visit.id).values(price=222));writer.commit()
    assert visit.price==999
    pending=Client(full_name='Keep pending');db.add(pending)
    fresh=lock_quote_update_target(db,visit)
    assert fresh.price==222 and pending.id is None
    db.rollback()


@pytest.mark.parametrize('kind',['staff','slot','business','visit','item'])
def test_update_target_busy_anchor_is_nowait_and_releases_all_earlier_anchors(db,world,kind):
    from app.api.core.visit_settlement_guard import lock_quote_update_target
    from app.db.models.user import User
    from app.db.models.visit_item import VisitItem
    engine=db.get_bind();ready=Event();release=Event()
    def holder():
        with Session(engine) as worker:
            if kind=='staff':worker.execute(select(User.id).where(User.id==world['staff']).with_for_update())
            elif kind=='slot':worker.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),{'key':f"visit-slot:{world['staff']}"})
            elif kind=='business':worker.execute(select(Business.id).where(Business.id==world['business']).with_for_update())
            elif kind=='visit':worker.execute(select(Visit.id).where(Visit.id==world['visit']).with_for_update())
            else:worker.execute(select(VisitItem.id).where(VisitItem.visit_id==world['visit']).with_for_update())
            ready.set();release.wait(5);worker.rollback()
    visit=db.get(Visit,world['visit'])
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(holder)
        try:
            assert ready.wait(5)
            with pytest.raises(SettlementBusy):lock_quote_update_target(db,visit)
            db.rollback()
        finally:release.set();db.rollback()
        future.result(timeout=5)
    assert lock_quote_update_target(db,visit).id==world['visit'];db.rollback()


def test_target_pending_booking_edits_are_rejected_without_erasing_them(db,world):
    from app.api.core.visit_settlement_guard import lock_quote_update_target
    visit=db.get(Visit,world['visit']);visit.price=123
    with pytest.raises(DirtySettlementState):lock_quote_update_target(db,visit)
    assert visit in db.dirty and visit.price==123;db.rollback()


@pytest.mark.parametrize('kind',['retarget','new_relationship'])
def test_pending_item_relationship_edits_are_never_refreshed_away(db,world,kind):
    from app.api.core.visit_settlement_guard import lock_quote_update_target
    from app.db.models.visit_item import VisitItem
    visit=db.get(Visit,world['visit'])
    if kind=='retarget':
        row=db.query(VisitItem).filter_by(visit_id=visit.id).one();row.visit_id=visit.id+100
    else:
        row=VisitItem(visit=visit,position=1,specialist_id=world['staff'],duration_minutes=10,
            date_start=visit.date_end,date_end=visit.date_end+timedelta(minutes=10));db.add(row)
    with pytest.raises(DirtySettlementState):lock_quote_update_target(db,visit)
    assert row in db.new or row in db.dirty;db.rollback()
