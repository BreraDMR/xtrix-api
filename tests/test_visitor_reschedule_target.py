"""Scheduling anchors and immutable stored chains on PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import timedelta
from threading import Event

import pytest
from sqlalchemy import event, select, text, update
from sqlalchemy.orm import Session

from app.api.core import crm_metering, visitor_action_target as targets
from app.api.core.visitor_reschedule_target import (
    RescheduleUnavailable, resolve_reschedule_target, revalidate_reschedule_target,
)
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.user import User
from app.db.models.visit import Visit
from app.db.models.visit_item import VisitItem
from tests import test_visitor_confirm_core as core
from tests import test_visitor_action_target as author

world = core.world


def resolve(db, w):
    return resolve_reschedule_target(db, w['raw'], w['visit'], author.NOW + timedelta(days=4))


def test_stored_chain_is_frozen_select_only_and_released_by_caller(db,world):
    before=core.snapshot(db,world);db.rollback();writes=[]
    def capture(conn,cursor,sql,*args):
        if sql.lstrip().upper().startswith(('UPDATE','DELETE','INSERT')):writes.append(sql)
    event.listen(db.get_bind(),'before_cursor_execute',capture)
    try: result=resolve(db,world)
    finally:event.remove(db.get_bind(),'before_cursor_execute',capture)
    assert not writes
    assert result.authority.visit_id==world['visit']
    assert result.lines[0].price==before[1][0]['price']
    assert result.lines[0].duration_minutes==before[1][0]['duration_minutes']
    with pytest.raises(FrozenInstanceError):result.signature=()
    assert revalidate_reschedule_target(db,world['raw'],result)==result.authority
    assert core.snapshot(db,world)==before
    db.rollback()
    with pytest.raises(targets.DirtyActionState):revalidate_reschedule_target(db,world['raw'],result)


@pytest.mark.parametrize('kind',['foreign','invalid','expired','retarget','deleted'])
def test_denied_targets_never_enter_quota(db,world,monkeypatch,kind):
    raw=world['raw'];error=targets.ActionTargetUnavailable
    if kind=='foreign':raw=world['other_raw']
    elif kind=='invalid':raw='invalid';error=targets.InvalidActionSession
    elif kind=='expired':world['clock'][0]+=timedelta(days=31);error=targets.InvalidActionSession
    elif kind=='retarget':db.get(Visit,world['visit']).client_id=world['other_client']
    else:db.get(Visit,world['visit']).deleted_at=author.NOW
    db.commit()
    def fail(*a,**k):raise AssertionError('foreign target reached quota')
    monkeypatch.setattr(crm_metering,'enforce_visit_quota',fail)
    with pytest.raises(error):resolve_reschedule_target(db,raw,world['visit'],author.NOW+timedelta(days=4))
    db.rollback()


@pytest.mark.parametrize('kind',['staff','slot','visit','item','business'])
def test_busy_anchor_refuses_without_waiting_and_whole_rollback_releases_earlier_locks(db,world,kind):
    engine=db.get_bind();ready=Event();release=Event()
    def holder():
        with Session(engine) as worker:
            if kind=='staff':worker.execute(select(User.id).where(User.id==world['staff']).with_for_update())
            elif kind=='slot':worker.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),{'key':f"visit-slot:{world['staff']}"})
            elif kind=='business':worker.execute(select(Business.id).where(Business.id==world['business']).with_for_update())
            elif kind=='visit':worker.execute(select(Visit.id).where(Visit.id==world['visit']).with_for_update())
            else:worker.execute(select(VisitItem.id).where(VisitItem.visit_id==world['visit']).with_for_update())
            ready.set();release.wait(5);worker.rollback()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(holder)
        try:
            assert ready.wait(5)
            with pytest.raises(targets.ActionBusy):resolve(db,world)
            db.rollback()
            with Session(engine) as observer:
                observer.execute(select(User.id).where(User.id==world['staff']).with_for_update(nowait=True)) if kind!='staff' else None
        finally:release.set()
        future.result(timeout=5)
    assert resolve(db,world).authority.visit_id==world['visit'];db.rollback()


def test_quota_precedes_staff_and_late_expiry_is_rejected(db,world,monkeypatch):
    reached=[]
    def quota(session,*a,**k):
        with Session(session.get_bind()) as observer:
            observer.execute(select(User.id).where(User.id==world['staff']).with_for_update(nowait=True))
        reached.append(True);world['clock'][0]+=timedelta(days=31)
    monkeypatch.setattr(crm_metering,'enforce_visit_quota',quota)
    with pytest.raises(targets.InvalidActionSession):resolve(db,world)
    assert reached;db.rollback()


def test_changed_chain_after_hint_is_retried_without_writing(db,world,monkeypatch):
    def quota(session,*a,**k):
        with Session(session.get_bind()) as worker:
            worker.execute(update(Visit).where(Visit.id==world['visit']).values(price=123));worker.commit()
    monkeypatch.setattr(crm_metering,'enforce_visit_quota',quota)
    with pytest.raises(targets.ActionBusy):resolve(db,world)
    db.rollback();assert db.get(Visit,world['visit']).price==123


def test_unrelated_pending_object_is_not_flushed(db,world):
    pending=Client(full_name='Do not flush');db.add(pending)
    resolve(db,world);assert pending.id is None;db.rollback()


@pytest.mark.parametrize('kind',['staff','item'])
def test_pending_schedule_edits_are_preserved(db,world,kind):
    if kind=='staff':row=db.get(User,world['staff']);row.full_name='Pending name'
    else:row=db.query(VisitItem).filter_by(visit_id=world['visit']).one();row.service_name='Pending name'
    with pytest.raises(targets.DirtyActionState):resolve(db,world)
    assert row in db.dirty;db.rollback()


def test_savepoint_rollback_invalidates_facts(db,world):
    nested=db.begin_nested();result=resolve(db,world);nested.rollback()
    with pytest.raises(targets.DirtyActionState):revalidate_reschedule_target(db,world['raw'],result)
    db.rollback()


def test_malformed_stored_chain_is_not_rebuilt_from_catalog(db,world):
    item=db.query(VisitItem).filter_by(visit_id=world['visit']).one();item.date_end+=timedelta(minutes=1);db.commit()
    with pytest.raises(RescheduleUnavailable):resolve(db,world)
    db.rollback()


def test_retargeted_hint_is_rejected_before_ordinary_resolver_can_lock_new_business(db,world,monkeypatch):
    original=targets.resolve_action_target;entered=[]
    def quota(session,*args,**kwargs):
        with Session(session.get_bind()) as worker:
            worker.execute(update(Visit).where(Visit.id==world['visit']).values(client_id=world['other_client']));worker.commit()
    def ordinary(*args,**kwargs):entered.append(True);return original(*args,**kwargs)
    monkeypatch.setattr(crm_metering,'enforce_visit_quota',quota)
    monkeypatch.setattr(targets,'resolve_action_target',ordinary)
    with pytest.raises(targets.ActionTargetUnavailable):resolve(db,world)
    assert not entered;db.rollback()
