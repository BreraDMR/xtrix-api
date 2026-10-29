"""HTTP move acknowledges a saved chain and never exposes financial internals."""
from datetime import timedelta

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.api.core import waitlist_service
from app.api.core.i18n import _
from app.db.models.user import User
from app.db.models.visit import Visit
from tests import test_visitor_reschedule_core as core
from tests import test_visitor_cancel_core as base

world=core.world


def url(w):return f"/visitor/visits/{w['visit']}/reschedule"
def headers(w,locale='ru'):return {'X-Visitor-Token':w['raw'],'Accept-Language':locale}
def payload(w):return {'start':w['new_start'].isoformat()+'Z'}
def private(r,status):
    assert r.status_code==status,r.text
    assert r.headers['cache-control']=='no-store'


def test_http_move_commits_before_slot_matching_and_repeat_is_noop(client,db,world,monkeypatch):
    calls=[]
    def released(_db,business,service_id,specialist_id,start):
        with Session(db.get_bind()) as observer:
            assert observer.get(Visit,world['visit']).date_start==world['new_start']
        calls.append(start)
    monkeypatch.setattr(waitlist_service,'release_slot_quietly',released)
    response=client.post(url(world),headers=headers(world),json=payload(world));private(response,204)
    assert response.content==b''
    first=base.state(db,world);db.rollback()
    private(client.post(url(world),headers=headers(world),json=payload(world)),204)
    assert base.state(db,world)==first and len(calls)==1


@pytest.mark.parametrize('credential',['missing','crm','foreign'])
def test_visitor_authority_cannot_be_substituted(client,db,world,credential):
    supplied={} if credential=='missing' else {'Authorization':'Bearer unrelated'}
    if credential=='foreign':supplied={'X-Visitor-Token':world['other_raw']}
    before=base.state(db,world);db.rollback()
    private(client.post(url(world),headers=supplied,json=payload(world)),404 if credential=='foreign' else 401)
    assert base.state(db,world)==before


@pytest.mark.parametrize('locale',['ru','en','cz'])
def test_cutoff_is_localized_and_history_still_readable(client,db,world,locale):
    world['clock'][0]+=timedelta(days=2,microseconds=1)
    response=client.post(url(world),headers=headers(world,locale),json=payload(world));private(response,409)
    assert response.json()['error']['message']==_('booking_reschedule_too_late',locale)
    private(client.get(f"/visitor/visits/{world['visit']}",headers=headers(world)),200)


def test_busy_staff_is_retryable_without_changes(client,db,world):
    before=base.state(db,world);db.rollback()
    with Session(db.get_bind()) as holder:
        holder.execute(select(User.id).where(User.id==world['staff']).with_for_update())
        response=client.post(url(world),headers=headers(world),json=payload(world));private(response,409)
        assert response.json()['error']['message']==_('visitor_action_retry','ru')
    assert base.state(db,world)==before


def test_post_commit_matching_failure_keeps_success(client,db,world,monkeypatch):
    def fail(*args):raise RuntimeError('synthetic matching failure')
    monkeypatch.setattr(waitlist_service,'release_slot_quietly',fail)
    private(client.post(url(world),headers=headers(world),json=payload(world)),204)
    with Session(db.get_bind()) as observer:assert observer.get(Visit,world['visit']).date_start==world['new_start']


@pytest.mark.parametrize('bad',[{}, {'start':'invalid'}, {'start':'2026-10-31T12:00:00Z','price':0}])
def test_invalid_or_extra_fields_never_change_the_booking(client,db,world,bad):
    before=base.state(db,world);db.rollback()
    private(client.post(url(world),headers=headers(world),json=bad),422)
    assert base.state(db,world)==before


def test_commit_failure_never_matches_slot_or_acknowledges_success(client,db,world,monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy import text
    from app.main import app
    calls=[];monkeypatch.setattr(waitlist_service,'release_slot_quietly',lambda *a:calls.append(a))
    db.execute(text('''CREATE FUNCTION reject_http_move() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
        IF NEW.event_type='rescheduled' THEN RAISE EXCEPTION 'synthetic commit failure'; END IF; RETURN NEW; END $$'''))
    db.execute(text('''CREATE CONSTRAINT TRIGGER reject_http_move AFTER INSERT ON visit_events
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION reject_http_move()'''));db.commit()
    before=base.state(db,world);db.rollback()
    try:
        with TestClient(app,raise_server_exceptions=False) as transport:
            private(transport.post(url(world),headers=headers(world),json=payload(world)),500)
        assert not calls
        with Session(db.get_bind()) as observer:assert base.state(observer,world)==before
    finally:
        db.rollback();db.execute(text('DROP TRIGGER reject_http_move ON visit_events'))
        db.execute(text('DROP FUNCTION reject_http_move()'));db.commit()


def test_utc_offset_is_normalized_before_scheduling(client,db,world):
    from datetime import timezone
    supplied=world['new_start'].replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=2))).isoformat()
    private(client.post(url(world),headers=headers(world),json={'start':supplied}),204)
    with Session(db.get_bind()) as observer:assert observer.get(Visit,world['visit']).date_start==world['new_start']


def test_extreme_destination_is_a_public_refusal_not_a_server_error(client,db,world):
    from fastapi.testclient import TestClient
    from app.main import app
    before=base.state(db,world);db.rollback()
    with TestClient(app,raise_server_exceptions=False) as transport:
        private(transport.post(url(world),headers=headers(world),json={'start':'9999-12-31T23:59:00Z'}),422)
    with Session(db.get_bind()) as observer:assert base.state(observer,world)==before
