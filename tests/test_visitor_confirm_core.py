from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import timedelta
from threading import Event

import pytest
from sqlalchemy import event, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.core import subscription_access, visitor_action_target as targets
from app.api.core import visitor_history, visitor_sessions
from app.api.core.booking_manage_policy import BookingActionNotAllowed
from app.api.core.visitor_confirm import confirm_visit
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.subscription import Subscription
from app.db.models.subscription_plan import SubscriptionPlan
from app.db.models.visit import Visit
from app.db.models.visit_item import VisitItem
from app.db.models.visitor_access import VisitorGrant
from app.db.models.visitor_account import VisitorAccount
from app.db.models.visitor_auth import VisitorSession
from tests import test_visitor_action_target as author


@pytest.fixture
def world(db,monkeypatch):
    w=author.world.__wrapped__(db,monkeypatch)
    plan=db.query(SubscriptionPlan).first()
    sub=Subscription(user_id=w['staff'],plan_id=plan.id,status='active',current_period_end=author.NOW+timedelta(days=5))
    db.add(sub);db.commit();w['sub']=sub.id;db.rollback()
    return w


def snapshot(db,w):
    return (dict(db.execute(select(Visit.__table__).where(Visit.id==w['visit'])).mappings().one()),
            [dict(r) for r in db.execute(select(VisitItem.__table__).where(VisitItem.visit_id==w['visit']).order_by(VisitItem.position)).mappings()])


def test_only_confirmation_field_changes_and_caller_commit_is_required(db,world):
    w=world;before=snapshot(db,w);db.rollback();statements=[]
    def capture(conn,cursor,statement,*args):
        if statement.lstrip().upper().startswith(('UPDATE','INSERT','DELETE')):statements.append(statement)
    event.listen(db.get_bind(),'before_cursor_execute',capture)
    try:result=confirm_visit(db,w['raw'],w['visit'])
    finally:event.remove(db.get_bind(),'before_cursor_execute',capture)
    assert result.id==w['visit'] and result.changed and result.confirmed_at==author.NOW
    with pytest.raises(FrozenInstanceError):result.changed=False
    assert len(statements)==1 and statements[0].split(' SET ')[1].split(' WHERE ')[0]=='client_confirmed_at=%(client_confirmed_at)s'
    with Session(db.get_bind()) as observer:assert observer.get(Visit,w['visit']).client_confirmed_at is None
    after=snapshot(db,w)
    assert {k for k in before[0] if before[0][k]!=after[0][k]}=={'client_confirmed_at'}
    assert before[1]==after[1]
    db.commit()
    with Session(db.get_bind()) as observer:assert observer.get(Visit,w['visit']).client_confirmed_at==result.confirmed_at


def test_whole_rollback_preserves_visit_items_price_and_confirm_proof(db,world):
    before=snapshot(db,world);db.rollback()
    assert confirm_visit(db,world['raw'],world['visit']).changed
    db.rollback()
    assert snapshot(db,world)==before


def test_repeat_is_current_authorized_noop_and_keeps_first_timestamp(db,world):
    w=world;first=confirm_visit(db,w['raw'],w['visit']);db.commit()
    w['clock'][0]+=timedelta(hours=1)
    again=confirm_visit(db,w['same_raw'],w['visit']);assert not again.changed and again.confirmed_at==first.confirmed_at
    db.commit()
    db.get(VisitorGrant,w['grant']).revoked_at=author.NOW;db.commit()
    with pytest.raises(targets.ActionTargetUnavailable):confirm_visit(db,w['raw'],w['visit'])
    db.rollback()


@pytest.mark.parametrize('kind',['foreign','revoked','deleted','retarget','invalid_session','expired_session'])
def test_denied_authority_never_enters_billing_or_changes_visit(db,world,monkeypatch,kind):
    w=world;raw=w['raw']
    expected=targets.ActionTargetUnavailable
    if kind=='foreign':raw=w['other_raw']
    elif kind=='revoked':db.get(VisitorGrant,w['grant']).revoked_at=author.NOW
    elif kind=='deleted':db.get(Visit,w['visit']).deleted_at=author.NOW
    elif kind=='retarget':db.get(Visit,w['visit']).client_id=w['other_client']
    elif kind=='invalid_session':raw='bad';expected=targets.InvalidActionSession
    else:w['clock'][0]=author.NOW+timedelta(days=31);expected=targets.InvalidActionSession
    db.commit()
    def fail(*args,**kwargs):raise AssertionError('Denied target entered billing')
    monkeypatch.setattr(subscription_access,'enforce_business_access',fail)
    with pytest.raises(expected):confirm_visit(db,raw,w['visit'])
    db.rollback();assert db.get(Visit,w['visit']).client_confirmed_at is None


@pytest.mark.parametrize('status,offset,reason',[
    ('planned',timedelta(),'booking_confirm_started'),
    ('planned',timedelta(microseconds=-1),'booking_confirm_started'),
    ('completed',timedelta(days=2),'booking_confirm_completed'),
    ('in_progress',timedelta(days=2),'booking_confirm_in_progress'),
    ('cancelled',timedelta(days=2),'booking_confirm_cancelled'),
    ('awaiting_payment',timedelta(days=2),'booking_confirm_unavailable'),
    (None,timedelta(days=2),'booking_confirm_unavailable'),
])
def test_status_and_start_refusals_are_typed(db,world,status,offset,reason):
    v=db.get(Visit,world['visit']);v.status=status;v.date_start=author.NOW+offset;v.date_end=v.date_start+timedelta(hours=1);db.commit()
    with pytest.raises(BookingActionNotAllowed) as exc:confirm_visit(db,world['raw'],world['visit'])
    assert exc.value.reason==reason;db.rollback()
    assert db.get(Visit,world['visit']).client_confirmed_at is None


@pytest.mark.parametrize('hours',[1,23,24])
def test_confirmation_has_no_cancellation_cutoff_or_catalog_requirement(db,world,monkeypatch,hours):
    from app.api.crud import crud_public_booking
    v=db.get(Visit,world['visit']);v.date_start=author.NOW+timedelta(hours=hours);v.date_end=v.date_start+timedelta(minutes=30);db.commit()
    def fail(*args,**kwargs):raise AssertionError('Confirm tried catalog or slot lookup')
    monkeypatch.setattr(crud_public_booking,'get_public_service',fail)
    assert confirm_visit(db,world['raw'],world['visit']).changed;db.rollback()


@pytest.mark.parametrize('mode',['expired','trial_readonly','no_entitlement'])
def test_blocked_crm_refuses_confirm_but_history_remains_readable(db,world,mode):
    sub=db.get(Subscription,world['sub'])
    if mode=='expired':sub.current_period_end=author.NOW-timedelta(seconds=1)
    elif mode=='trial_readonly':sub.is_trial=True;sub.trial_end=author.NOW-timedelta(hours=1)
    else:db.delete(sub)
    db.commit()
    with pytest.raises(BookingActionNotAllowed) as exc:confirm_visit(db,world['raw'],world['visit'])
    assert exc.value.reason=='visitor_actions_unavailable';db.rollback()
    assert visitor_history.get_visit(db,world['account'],world['visit']) is not None


def test_dirty_unrelated_object_is_not_flushed_and_clean_cached_visit_is_not_refreshed(db,world):
    cached=db.get(Visit,world['visit']);assert cached.client_confirmed_at is None
    pending=Client(full_name='Pending untouched client');db.add(pending)
    assert confirm_visit(db,world['raw'],world['visit']).changed
    assert pending.id is None and pending in db.new
    assert cached.client_confirmed_at is None
    with Session(db.get_bind()) as observer:assert observer.query(Client).filter_by(full_name=pending.full_name).count()==0
    db.rollback();assert pending.id is None


def test_time_is_rechecked_after_real_billing_reads_before_update(db,world):
    w=world;v=db.get(Visit,w['visit']);v.date_start=author.NOW+timedelta(seconds=1);v.date_end=v.date_start+timedelta(minutes=30);db.commit()
    engine=db.get_bind();calls=[]
    def after(conn,cursor,statement,*args):
        if statement.lstrip().upper().startswith('SELECT') and 'subscriptions' in statement:
            calls.append(statement);w['clock'][0]=author.NOW+timedelta(seconds=1)
    event.listen(engine,'after_cursor_execute',after)
    try:
        with pytest.raises(BookingActionNotAllowed) as exc:confirm_visit(db,w['raw'],w['visit'])
    finally:event.remove(engine,'after_cursor_execute',after)
    assert calls and exc.value.reason=='booking_confirm_started';db.rollback()
    assert db.get(Visit,w['visit']).client_confirmed_at is None


def test_late_clock_after_actual_update_requires_whole_rollback(db,world):
    w=world;v=db.get(Visit,w['visit']);v.date_start=author.NOW+timedelta(seconds=1);v.date_end=v.date_start+timedelta(minutes=30);db.commit()
    updates=[]
    def after(conn,cursor,statement,*args):
        if statement.lstrip().startswith('UPDATE visits SET client_confirmed_at'):
            updates.append(statement);w['clock'][0]=author.NOW+timedelta(seconds=1)
    event.listen(db.get_bind(),'after_cursor_execute',after)
    try:
        with pytest.raises(BookingActionNotAllowed) as exc:confirm_visit(db,w['raw'],w['visit'])
    finally:event.remove(db.get_bind(),'after_cursor_execute',after)
    assert len(updates)==1 and exc.value.reason=='booking_confirm_started'
    db.rollback();assert db.get(Visit,w['visit']).client_confirmed_at is None


def test_same_account_two_sessions_serialize_and_only_one_changes(db,world):
    w=world;engine=db.get_bind();first=Session(engine)
    assert confirm_visit(first,w['raw'],w['visit']).changed
    ready=Event();pid=[]
    def worker():
        with Session(engine) as second:
            second.execute(text("SET LOCAL lock_timeout='4s'"))
            pid.append(second.execute(text('SELECT pg_backend_pid()')).scalar_one());ready.set()
            result=confirm_visit(second,w['same_raw'],w['visit']);second.commit();return result
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(worker);assert ready.wait(3)
            assert author.wait_blocked(engine,pid[0])
            first.commit();result=future.result(timeout=5)
        assert not result.changed and result.confirmed_at==author.NOW
    finally:first.rollback();first.close()


def test_account_wait_expiry_fails_before_confirm(db,world):
    w=world;engine=db.get_bind();holder=Session(engine)
    holder.execute(select(VisitorAccount.id).where(VisitorAccount.id==w['account']).with_for_update())
    ready=Event();pid=[]
    def worker():
        with Session(engine) as second:
            second.execute(text("SET LOCAL lock_timeout='4s'"))
            pid.append(second.execute(text('SELECT pg_backend_pid()')).scalar_one());ready.set()
            try:confirm_visit(second,w['raw'],w['visit'])
            except targets.InvalidActionSession:second.rollback();return 'expired'
            return 'unexpected'
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(worker);assert ready.wait(3);assert author.wait_blocked(engine,pid[0])
            w['clock'][0]=author.NOW+timedelta(days=31);holder.commit()
            assert future.result(timeout=5)=='expired'
    finally:holder.rollback();holder.close()
    assert db.get(Visit,w['visit']).client_confirmed_at is None


def test_deferred_commit_failure_cannot_leave_confirmation_half_committed(db,world):
    # Synthetic deferred guard exercises caller-owned commit failure on real PG.
    db.execute(text('''CREATE FUNCTION test_reject_confirm() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'synthetic deferred reject' USING ERRCODE='23514'; END $$'''))
    db.execute(text('''CREATE CONSTRAINT TRIGGER test_reject_confirm AFTER UPDATE OF client_confirmed_at ON visits
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION test_reject_confirm()'''));db.commit()
    try:
        assert confirm_visit(db,world['raw'],world['visit']).changed
        with pytest.raises(DBAPIError):db.commit()
        db.rollback()
        with Session(db.get_bind()) as observer:assert observer.get(Visit,world['visit']).client_confirmed_at is None
    finally:
        db.rollback();db.execute(text('DROP TRIGGER test_reject_confirm ON visits'));db.execute(text('DROP FUNCTION test_reject_confirm()'));db.commit()


def test_billing_change_after_actual_update_denies_and_confirmation_rolls_back(db,world):
    w=world;engine=db.get_bind();observed=[]
    def after(conn,cursor,statement,*args):
        if statement.lstrip().startswith('UPDATE visits SET client_confirmed_at'):
            observed.append(statement)
            with Session(engine) as writer:
                writer.execute(update(Subscription).where(Subscription.id==w['sub']).values(status='cancelled'));writer.commit()
    event.listen(engine,'after_cursor_execute',after)
    try:
        with pytest.raises(BookingActionNotAllowed) as exc:confirm_visit(db,w['raw'],w['visit'])
    finally:event.remove(engine,'after_cursor_execute',after)
    assert len(observed)==1 and exc.value.reason=='visitor_actions_unavailable'
    db.rollback();assert db.get(Visit,w['visit']).client_confirmed_at is None
    assert db.get(Subscription,w['sub']).status=='cancelled'


@pytest.mark.parametrize('change',['started','unpaid'])
def test_existing_confirmation_is_not_early_return_before_current_policy(db,world,change):
    w=world;assert confirm_visit(db,w['raw'],w['visit']).changed;db.commit()
    if change=='started':w['clock'][0]=author.NOW+timedelta(days=3)
    else:db.get(Subscription,w['sub']).status='cancelled';db.commit()
    with pytest.raises(BookingActionNotAllowed) as exc:confirm_visit(db,w['raw'],w['visit'])
    assert exc.value.reason==('booking_confirm_started' if change=='started' else 'visitor_actions_unavailable')
    db.rollback();assert db.get(Visit,w['visit']).client_confirmed_at==author.NOW
