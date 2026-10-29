"""Every old master segment is considered after a saved move or cancellation."""
from datetime import time,timedelta

import pytest
from sqlalchemy import select,text
from sqlalchemy.orm import Session

from app.api.core import waitlist_service,visitor_action_target as targets
from app.api.core.visitor_cancel import cancel_visit
from app.db.models.service import Service
from app.db.models.user import User
from app.db.models.visit import Visit
from app.db.models.visit_item import VisitItem
from app.db.models.work_schedule import WorkSchedule
from tests import test_visitor_reschedule_core as base


@pytest.fixture
def world(db,monkeypatch):
    w=base.world.__wrapped__(db,monkeypatch)
    second=User(full_name='Second released master',email='second.released@example.com',role='business_owner',
        hashed_password='synthetic',business_id=w['business']);db.add(second);db.flush()
    service=Service(business_id=w['business'],specialist_id=second.id,name='Second segment',duration_minutes=10,price=5)
    db.add(service);db.flush();visit=db.get(Visit,w['visit']);old_end=visit.date_end
    db.add(VisitItem(visit_id=visit.id,position=1,service_id=service.id,specialist_id=second.id,
        service_name='Second sold segment',price=321,duration_minutes=40,buffer_before_minutes=5,
        buffer_after_minutes=15,date_start=old_end,date_end=old_end+timedelta(minutes=40)))
    visit.date_end=old_end+timedelta(minutes=40)
    for day in range(7):db.add(WorkSchedule(specialist_id=second.id,weekday=day,start_time=time(0),end_time=time(23,59)))
    db.commit()
    w['slots']=[(row.service_id,row.specialist_id,row.date_start) for row in db.execute(select(
        VisitItem.service_id,VisitItem.specialist_id,VisitItem.date_start).where(VisitItem.visit_id==visit.id).order_by(VisitItem.position))]
    db.rollback();return w


@pytest.mark.parametrize('action',['cancel','reschedule','crm_cancel','crm_move'])
@pytest.mark.parametrize('fail_first',[False,True])
def test_matching_visits_each_saved_old_segment_even_after_one_matcher_fails(client,db,world,headers_for,monkeypatch,action,fail_first):
    w=world;calls=[]
    def released(session,business,service_id,master_id,start):
        with Session(db.get_bind()) as observer:
            saved=observer.get(Visit,w['visit'])
            assert saved.status=='cancelled' if action.endswith('cancel') else saved.date_start==w['new_start']
        calls.append((service_id,master_id,start))
        if fail_first and len(calls)==1:raise RuntimeError('Synthetic failure of the first released segment')
    monkeypatch.setattr(waitlist_service,'release_slot_quietly',released)
    if action.startswith('crm_'):
        body={'status':'cancelled'} if action=='crm_cancel' else {'date_start':w['new_start'].isoformat()}
        response=client.put(f"/visits/{w['visit']}",headers=headers_for(db.get(User,w['staff'])),json=body)
        expected_status=200
    else:
        body={} if action=='cancel' else {'start':w['new_start'].isoformat()+'Z'}
        response=client.post(f"/visitor/visits/{w['visit']}/{action}",headers={'X-Visitor-Token':w['raw']},json=body)
        expected_status=204
    assert response.status_code==expected_status,response.text
    assert calls==w['slots']
    # A repeated saved command cannot offer the same old segments a second time.
    if action.startswith('crm_'):
        response=client.put(f"/visits/{w['visit']}",headers=headers_for(db.get(User,w['staff'])),json=body)
    else:
        response=client.post(f"/visitor/visits/{w['visit']}/{action}",headers={'X-Visitor-Token':w['raw']},json=body)
    assert response.status_code==expected_status,response.text
    assert calls==w['slots']


def test_cancellation_retries_instead_of_waiting_behind_a_busy_item(db,world):
    item_id=db.execute(select(VisitItem.id).where(VisitItem.visit_id==world['visit']).order_by(VisitItem.position)).first()[0]
    db.rollback()
    with Session(db.get_bind()) as holder:
        holder.execute(select(VisitItem.id).where(VisitItem.id==item_id).with_for_update())
        db.execute(text("SET LOCAL lock_timeout='500ms'"))
        try:
            with pytest.raises(targets.ActionBusy):cancel_visit(db,world['raw'],world['visit'])
        finally:db.rollback()
    assert db.get(Visit,world['visit']).status=='planned'


def test_cancellation_metadata_keeps_each_original_interval_and_is_immutable(db,world):
    from dataclasses import FrozenInstanceError
    rows=db.execute(select(VisitItem.service_id,VisitItem.specialist_id,VisitItem.date_start,VisitItem.date_end)
        .where(VisitItem.visit_id==world['visit']).order_by(VisitItem.position)).all();db.rollback()
    result=cancel_visit(db,world['raw'],world['visit'])
    assert [(slot.service_id,slot.specialist_id,slot.start,slot.end) for slot in result.released_slots]==[tuple(row) for row in rows]
    with pytest.raises(FrozenInstanceError):result.released_slots[0].start=world['new_start']
    db.rollback();assert db.get(Visit,world['visit']).status=='planned'


def test_dirty_item_is_rejected_without_flushing_or_refreshing_it(db,world):
    item=db.query(VisitItem).filter_by(visit_id=world['visit'],position=1).one();old_price=item.price
    item.price=1
    with pytest.raises(targets.DirtyActionState):cancel_visit(db,world['raw'],world['visit'])
    assert item in db.dirty and item.price==1
    with Session(db.get_bind()) as observer:assert observer.get(VisitItem,item.id).price==old_price
    db.rollback()


def test_repeated_cancellation_does_not_lock_children_or_emit_new_slots(db,world):
    first=cancel_visit(db,world['raw'],world['visit']);assert len(first.released_slots)==2;db.commit()
    item_id=db.execute(select(VisitItem.id).where(VisitItem.visit_id==world['visit'])).first()[0];db.rollback()
    with Session(db.get_bind()) as holder:
        holder.execute(select(VisitItem.id).where(VisitItem.id==item_id).with_for_update())
        db.execute(text("SET LOCAL lock_timeout='500ms'"))
        result=cancel_visit(db,world['raw'],world['visit'])
        assert not result.changed and result.released_slots==()
        db.rollback()
