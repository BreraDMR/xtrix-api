"""Moving a sold quote changes timestamps only and shares the caller transaction."""
from datetime import time, timedelta

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.core import availability, notification_events, subscription_access
from app.api.core import visitor_action_target as targets
from app.api.core.booking_manage_policy import BookingActionNotAllowed
from app.api.core.visitor_reschedule import reschedule_visit
from app.db.models.business import Business
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.service import Service
from app.db.models.subscription import Subscription
from app.db.models.visit import Visit
from app.db.models.visit_event import VisitEvent
from app.db.models.visit_item import VisitItem
from app.db.models.work_schedule import WorkSchedule
from tests import test_visitor_cancel_core as base
from tests import test_visitor_action_target as author


@pytest.fixture
def world(db,monkeypatch):
    w=base.world.__wrapped__(db,monkeypatch)
    monkeypatch.setattr(availability,'utcnow',lambda:w['clock'][0])
    monkeypatch.setattr(notification_events,'utcnow',lambda:w['clock'][0])
    svc=Service(business_id=w['business'],specialist_id=w['staff'],name='New catalog name',
        duration_minutes=15,price=40,buffer_before_minutes=30,buffer_after_minutes=30)
    db.add(svc);db.flush();w['service']=svc.id
    v=db.get(Visit,w['visit']);v.service_id=svc.id;v.service_name='Sold name';v.currency_snapshot='CZK'
    item=db.query(VisitItem).filter_by(visit_id=v.id).one();item.service_id=svc.id;item.service_name='Sold name'
    item.price=999;item.buffer_before_minutes=5;item.buffer_after_minutes=10
    db.get(Business,w['business']).currency='EUR'
    for day in range(7):db.add(WorkSchedule(specialist_id=w['staff'],weekday=day,start_time=time(0),end_time=time(23,59)))
    db.commit();w['new_start']=author.NOW+timedelta(days=4)
    return w


def move(db,w,start=None):return reschedule_visit(db,w['raw'],w['visit'],start or w['new_start'])


def test_atomic_move_preserves_entire_sold_quote_and_payments(db,world):
    w=world;before=base.state(db,w);db.rollback();result=move(db,w);after=base.state(db,w)
    assert result.changed and result.start==before['visit'][0]['date_start']
    assert {k for k in before['visit'][0] if before['visit'][0][k]!=after['visit'][0][k]}=={'date_start','date_end'}
    assert after['visit'][0]['price']==999 and after['visit'][0]['currency_snapshot']=='CZK'
    for old,new in zip(before['visit'][1],after['visit'][1]):
        assert {k:v for k,v in old.items() if k not in ('date_start','date_end')}=={k:v for k,v in new.items() if k not in ('date_start','date_end')}
    assert after['attempts']==before['attempts']
    assert len(after['events'])==1 and after['events'][0]['event_type']=='rescheduled'
    assert len([r for r in after['outbox'] if r['kind']=='booking_changed'])==1
    assert any(r['kind']=='booking_reminder' and r['status']=='pending' for r in after['outbox'])
    with Session(db.get_bind()) as observer:assert base.state(observer,w)==before
    db.commit()
    assert not move(db,w).changed;db.commit()
    assert base.state(db,w)==after


def test_whole_rollback_restores_timestamps_payments_events_and_outbox(db,world):
    before=base.state(db,world);db.rollback();move(db,world);db.rollback();assert base.state(db,world)==before


@pytest.mark.parametrize('kind',['foreign','expired','unpaid','late','cancelled','catalog','past_destination','closed_schedule'])
def test_refusal_never_changes_any_booking_state(db,world,kind):
    w=world;raw=w['raw'];expected=BookingActionNotAllowed
    if kind=='foreign':raw=w['other_raw'];expected=targets.ActionTargetUnavailable
    elif kind=='expired':w['clock'][0]+=timedelta(days=31);expected=targets.InvalidActionSession
    elif kind=='unpaid':db.get(Subscription,w['sub']).status='cancelled'
    elif kind=='late':w['clock'][0]+=timedelta(days=2,microseconds=1)
    elif kind=='cancelled':db.get(Visit,w['visit']).status='cancelled'
    elif kind=='catalog':db.get(Service,w['service']).is_active=False
    elif kind=='past_destination':w['new_start']=author.NOW-timedelta(minutes=1)
    else:db.query(WorkSchedule).delete()
    db.commit();before=base.state(db,w);db.rollback()
    with pytest.raises(expected):reschedule_visit(db,raw,w['visit'],w['new_start'])
    db.rollback();assert base.state(db,w)==before


def test_real_busy_slot_is_rejected_even_if_current_catalog_duration_would_fit(db,world):
    w=world
    # The sold chain is 60min plus 10min after; today's 15min catalog would fit.
    other=Visit(business_id=w['business'],client_id=w['other_client'],specialist_id=w['staff'],
        date_start=w['new_start']+timedelta(minutes=50),date_end=w['new_start']+timedelta(minutes=80),status='planned')
    db.add(other);db.commit();before=base.state(db,w);db.rollback()
    with pytest.raises(BookingActionNotAllowed) as exc:move(db,w)
    assert exc.value.reason=='public_slot_unavailable';db.rollback();assert base.state(db,w)==before


def test_expiry_after_actual_update_requires_rollback(db,world):
    before=base.state(db,world);db.rollback()
    def after(conn,cursor,sql,*args):
        if sql.startswith('UPDATE visits SET date_start'):world['clock'][0]+=timedelta(days=31)
    event.listen(db.get_bind(),'after_cursor_execute',after)
    try:
        with pytest.raises(targets.InvalidActionSession):move(db,world)
    finally:event.remove(db.get_bind(),'after_cursor_execute',after)
    db.rollback();assert base.state(db,world)==before


def test_original_cutoff_is_checked_after_update_not_new_later_start(db,world):
    before=base.state(db,world);db.rollback()
    def after(conn,cursor,sql,*args):
        if sql.startswith('UPDATE visits SET date_start'):world['clock'][0]+=timedelta(days=2,microseconds=1)
    event.listen(db.get_bind(),'after_cursor_execute',after)
    try:
        with pytest.raises(BookingActionNotAllowed) as exc:move(db,world)
        assert exc.value.reason=='booking_reschedule_too_late'
    finally:event.remove(db.get_bind(),'after_cursor_execute',after)
    db.rollback();assert base.state(db,world)==before


def test_queue_failure_rolls_back_the_move(db,world,monkeypatch):
    before=base.state(db,world);db.rollback();original=notification_events.queue_booking_changed
    def fail(*args):original(*args);raise RuntimeError('synthetic failure')
    monkeypatch.setattr(notification_events,'queue_booking_changed',fail)
    with pytest.raises(RuntimeError):move(db,world)
    db.rollback();assert base.state(db,world)==before


def test_actual_deferred_commit_failure_rolls_back_whole_move(db,world):
    db.execute(text('''CREATE FUNCTION reject_visitor_move() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
        IF NEW.event_type = 'rescheduled' THEN RAISE EXCEPTION 'synthetic deferred move rejection'; END IF;
        RETURN NEW; END $$'''))
    db.execute(text('''CREATE CONSTRAINT TRIGGER reject_visitor_move AFTER INSERT ON visit_events
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION reject_visitor_move()'''));db.commit()
    try:
        before=base.state(db,world);db.rollback();move(db,world)
        with pytest.raises(DBAPIError):db.commit()
        db.rollback();assert base.state(db,world)==before
    finally:
        db.rollback();db.execute(text('DROP TRIGGER reject_visitor_move ON visit_events'))
        db.execute(text('DROP FUNCTION reject_visitor_move()'));db.commit()


def test_session_expiry_during_last_catalog_read_is_rechecked(db,world):
    before=base.state(db,world);db.rollback();moved=[False]
    def after(conn,cursor,sql,*args):
        if sql.startswith('UPDATE visits SET date_start'):moved[0]=True
        if moved[0] and 'FROM service_specialists' in sql:
            world['clock'][0]=author.NOW+timedelta(days=31)
    event.listen(db.get_bind(),'after_cursor_execute',after)
    try:
        with pytest.raises(targets.InvalidActionSession):move(db,world)
    finally:event.remove(db.get_bind(),'after_cursor_execute',after)
    db.rollback();assert base.state(db,world)==before


def test_multi_master_chain_keeps_ids_totals_and_each_sold_interval(db,world):
    from app.db.models.user import User
    w=world
    second=User(full_name='Second master',email='second-synthetic@example.test',role='business_owner',
        hashed_password='synthetic',business_id=w['business']);db.add(second);db.flush()
    svc=Service(business_id=w['business'],specialist_id=second.id,name='Second catalog',duration_minutes=10,price=5)
    db.add(svc);db.flush();v=db.get(Visit,w['visit']);old_end=v.date_end
    db.add(VisitItem(visit_id=v.id,position=1,service_id=svc.id,specialist_id=second.id,
        service_name='Second sold name',price=321,duration_minutes=40,buffer_before_minutes=5,
        buffer_after_minutes=15,date_start=old_end,date_end=old_end+timedelta(minutes=40)))
    v.date_end=old_end+timedelta(minutes=40)
    for day in range(7):db.add(WorkSchedule(specialist_id=second.id,weekday=day,start_time=time(0),end_time=time(23,59)))
    db.commit();before=base.state(db,w);db.rollback();move(db,w);db.commit();after=base.state(db,w)
    assert after['visit'][0]['price']==before['visit'][0]['price']==999
    delta=w['new_start']-before['visit'][0]['date_start']
    assert len(after['visit'][1])==2
    for old,new in zip(before['visit'][1],after['visit'][1]):
        assert old['date_start']+delta==new['date_start'] and old['date_end']+delta==new['date_end']
        assert {k:v for k,v in old.items() if k not in ('date_start','date_end')}=={k:v for k,v in new.items() if k not in ('date_start','date_end')}


def test_move_back_revives_cached_reminder_from_database_status(db,world):
    w=world;first_start=db.get(Visit,w['visit']).date_start
    move(db,w);db.commit()
    reminder_b=db.query(NotificationOutbox).filter_by(visit_id=w['visit'],kind='booking_reminder',status='pending').one()
    reminder_b_id=reminder_b.id
    move(db,w,first_start);db.commit()
    # Hold a deliberately stale cached status; the next command must refresh it.
    reminder_b=db.get(NotificationOutbox,reminder_b_id)
    with Session(db.get_bind()) as worker:
        from sqlalchemy import update
        worker.execute(update(NotificationOutbox).where(NotificationOutbox.id==reminder_b_id).values(status='pending'));worker.commit()
    assert reminder_b.status=='cancelled'
    move(db,w);db.commit()
    with Session(db.get_bind()) as observer:
        assert observer.get(NotificationOutbox,reminder_b_id).status=='pending'
        assert observer.query(VisitEvent).filter_by(visit_id=w['visit'],event_type='rescheduled').count()==3


def test_final_lead_time_uses_clock_after_queue(db,world):
    w=world;db.get(Business,w['business']).booking_lead_time_minutes=60;db.commit()
    w['new_start']=author.NOW+timedelta(minutes=60,seconds=1)
    before=base.state(db,w);db.rollback()
    def after(conn,cursor,sql,*args):
        if sql.startswith('UPDATE visits SET date_start'):w['clock'][0]+=timedelta(seconds=2)
    event.listen(db.get_bind(),'after_cursor_execute',after)
    try:
        with pytest.raises(BookingActionNotAllowed) as exc:move(db,w)
        assert exc.value.reason=='public_slot_unavailable'
    finally:event.remove(db.get_bind(),'after_cursor_execute',after)
    db.rollback();assert base.state(db,w)==before


def test_committed_schedule_change_overrides_cached_caller_hours(db,world):
    from sqlalchemy import update
    w=world;schedule=db.query(WorkSchedule).filter_by(specialist_id=w['staff'],weekday=w['new_start'].weekday()).one()
    assert schedule.end_time==time(23,59)
    with Session(db.get_bind()) as worker:
        worker.execute(update(WorkSchedule).where(WorkSchedule.id==schedule.id).values(end_time=time(10)));worker.commit()
    assert schedule.end_time==time(23,59)
    with pytest.raises(BookingActionNotAllowed) as exc:move(db,w)
    assert exc.value.reason=='public_slot_unavailable';db.rollback()


def test_two_sessions_serialize_one_move_and_one_event(db,world):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    w=world;move(db,w);ready=Event();holder={};engine=db.get_bind()
    def repeat():
        with Session(engine) as worker:
            holder['pid']=worker.execute(text('SELECT pg_backend_pid()')).scalar_one();ready.set()
            result=reschedule_visit(worker,w['same_raw'],w['visit'],w['new_start']);worker.commit();return result
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(repeat)
        try:
            assert ready.wait(5);assert author.wait_blocked(engine,holder['pid']);db.commit()
        finally:db.rollback()
        assert not future.result(timeout=5).changed
    assert db.query(VisitEvent).filter_by(visit_id=w['visit'],event_type='rescheduled').count()==1


def test_dirty_target_reminder_is_preserved_and_rejected(db,world):
    row=db.query(NotificationOutbox).filter_by(visit_id=world['visit'],kind='booking_reminder').one()
    row.recipient='pending-edit@example.test'
    with pytest.raises(targets.DirtyActionState):move(db,world)
    assert row.recipient=='pending-edit@example.test' and row in db.dirty
    db.rollback()
