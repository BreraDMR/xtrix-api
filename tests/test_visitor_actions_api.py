from datetime import timedelta
import importlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text, update
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core import subscription_access, visitor_sessions
from app.api.core.clock import utcnow
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.public_booking import PublicBookingSettings
from app.db.models.service import Service
from app.db.models.subscription import Subscription
from app.db.models.subscription_plan import SubscriptionPlan
from app.db.models.user import User
from app.db.models.visit import Visit
from app.db.models.visit_item import VisitItem
from app.db.models.visitor_access import VisitorGrant
from app.db.models.visitor_auth import VisitorSession
from tests import test_visitor_history as base
from tests.test_visitor_history_api import access, private

world = base.world


@pytest.fixture
def action_world(db,world,monkeypatch):
    w=world; w['now']=utcnow()
    plan=db.query(SubscriptionPlan).first()
    db.add(Subscription(user_id=w['staff_a'],plan_id=plan.id,status='active',current_period_end=w['now']+timedelta(days=5)))
    service=Service(name='Current service',specialist_id=w['staff_a'],business_id=w['a'],duration_minutes=30,is_active=True)
    db.add(service);db.flush();w['service']=service.id
    visit=db.get(Visit,w['a1']);visit.service_id=service.id;visit.date_start=w['now']+timedelta(days=2)
    visit.date_end=visit.date_start+timedelta(minutes=30)
    db.query(VisitItem).filter_by(visit_id=visit.id).update({'service_id':service.id})
    db.commit()
    endpoint=importlib.import_module('app.api.v1.endpoints.visitor_history')
    monkeypatch.setattr(endpoint,'utcnow',lambda:w['now'],raising=False)
    return w


def url(w,key='a1'):return f"/visitor/visits/{w.get(key,987654321)}/actions"


def test_authorized_actions_are_explicit_allowlist_and_do_not_change_history(client,db,action_world):
    w=action_world; headers,*_=access(db,w);base.grant(db,w,visit=w['a1']);db.commit()
    response=client.get(url(w),headers=headers);private(response,200)
    assert response.json()=={key:{'allowed':True,'reason':None} for key in ('confirm','cancel','reschedule')}
    assert 'private-' not in response.text and headers['X-Visitor-Token'] not in response.text
    history=client.get(f"/visitor/visits/{w['a1']}",headers=headers);private(history,200)
    assert 'actions' not in history.json() and 'allowed_actions' not in history.json()


@pytest.mark.parametrize('key',['a2','b1','a_other','solo','missing'])
def test_unauthorized_target_is_same404_before_any_billing_or_catalog(client,db,action_world,monkeypatch,key):
    from app.api.crud import crud_public_booking
    w=action_world;headers,*_=access(db,w);base.grant(db,w,visit=w['a1']);db.commit()
    def forbidden(*args,**kwargs):raise AssertionError('Foreign target entered billing/catalog')
    monkeypatch.setattr(subscription_access,'enforce_business_access',forbidden)
    monkeypatch.setattr(crud_public_booking,'get_public_service',forbidden)
    private(client.get(url(w,key),headers=headers),404)


@pytest.mark.parametrize('change',['revoked','grant_deleted','visit_deleted','client_deleted','business_deleted','retarget_client','retarget_business'])
def test_latest_unavailable_scope_is_same404_before_billing(client,db,action_world,monkeypatch,change):
    w=action_world;headers,*_=access(db,w);gid=base.grant(db,w,visit=w['a1']);db.commit()
    if change=='revoked':db.get(VisitorGrant,gid).revoked_at=w['now']
    elif change=='grant_deleted':db.delete(db.get(VisitorGrant,gid))
    elif change=='visit_deleted':db.get(Visit,w['a1']).deleted_at=w['now']
    elif change=='client_deleted':db.get(Client,w['client']).deleted_at=w['now']
    elif change=='business_deleted':db.get(Business,w['a']).deleted_at=w['now']
    else:setattr(db.get(Visit,w['a1']),'client_id' if change=='retarget_client' else 'business_id',w['other_client'] if change=='retarget_client' else w['b'])
    db.commit()
    def forbidden(*args,**kwargs):raise AssertionError('Denied target entered billing')
    monkeypatch.setattr(subscription_access,'enforce_business_access',forbidden)
    private(client.get(url(w),headers=headers),404)


@pytest.mark.parametrize('locale',['en','ru','cz'])
def test_404_is_existing_localized_history_error(client,db,action_world,locale):
    from app.api.core.i18n import _ as translate
    headers,*_=access(db,action_world);headers['Accept-Language']=locale
    response=client.get(url(action_world),headers=headers);private(response,404)
    assert response.json()['error']['message']==translate('visitor_history_visit_unavailable',locale)


@pytest.mark.parametrize('path',['/visitor/visits/0/actions','/visitor/visits/-1/actions','/visitor/visits/not-id/actions'])
def test_positive_path_validated_privately(client,db,action_world,path):
    headers,*_=access(db,action_world)
    private(client.get(path,headers=headers),422)


def test_strict_visitor_session_cannot_be_replaced_by_crm_or_query_account(client,db,action_world):
    from app.api.core.security import create_access_token
    w=action_world;base.grant(db,w);db.commit()
    for headers in ({},{'Authorization':'Bearer '+create_access_token({'sub':'staff.a@example.com'})}):
        private(client.get(url(w),headers=headers,params={'account_id':w['one']}),401)
    other=dict(w,one=w['two']);headers,*_=access(db,other)
    private(client.get(url(w),headers=headers,params={'account_id':w['one']}),404)


def test_readonly_http_does_not_autoflush_unrelated_dirty_object(db,client,action_world):
    from app.main import app
    w=action_world;headers,*_=access(db,w);base.grant(db,w);db.commit()
    person=db.get(User,w['staff_b']);person.full_name='Pending unrelated edit'
    conn=db.connection();conn.execute(text('SET TRANSACTION READ ONLY'))
    app.dependency_overrides[deps.get_db]=lambda:db
    statements=[]
    def capture(_conn,_cursor,statement,*args):statements.append(statement)
    event.listen(conn,'before_cursor_execute',capture)
    try:
        response=client.get(url(w),headers=headers);private(response,200)
        assert statements and all(s.lstrip().upper().startswith('SELECT') for s in statements)
        assert person.full_name=='Pending unrelated edit' and person in db.dirty
        assert conn.in_transaction()
    finally:
        event.remove(conn,'before_cursor_execute',capture)
        app.dependency_overrides.pop(deps.get_db,None)
        db.rollback()


def test_projection_is_one_authorized_sql_and_items_replace_legacy_parent_pair(db,client,action_world):
    from app.api.core import visitor_action_facts
    w=action_world;headers,*_=access(db,w);base.grant(db,w);db.commit()
    visit=db.get(Visit,w['a1']);visit.service_id=None
    visit.status='cancelled';visit.date_end=visit.date_start+timedelta(minutes=90)
    db.query(VisitItem).filter_by(visit_id=visit.id).delete()
    other=Service(name='Second stored service',specialist_id=w['staff_a'],business_id=w['a'],duration_minutes=30,is_active=True)
    db.add(other);db.flush();other_id=other.id
    base.grant(db,w,visit=w['a1'])
    for position in (2,0,1):
        db.add(VisitItem(visit_id=visit.id,position=position,service_id=other_id if position==1 else w['service'],specialist_id=w['staff_a'],
                         duration_minutes=30,active=False,date_start=visit.date_start+timedelta(minutes=position*30),
                         date_end=visit.date_start+timedelta(minutes=(position+1)*30)))
    db.commit()
    statements=[]
    def capture(_conn,_cursor,statement,*args):
        if 'authorized_action_visit' in statement:statements.append(statement)
    event.listen(db.get_bind(),'before_cursor_execute',capture)
    try:
        target=visitor_action_facts.get_authorized_action_target(db,w['one'],w['a1'])
    finally:event.remove(db.get_bind(),'before_cursor_execute',capture)
    assert len(statements)==1 and 'EXISTS' in statements[0]
    assert [(p.service_id,p.master_id) for p in target.service_master_pairs]==[(w['service'],w['staff_a']),(other_id,w['staff_a']),(w['service'],w['staff_a'])]
    private(client.get(url(w),headers=headers),200)
    assert client.get(url(w),headers=headers).json()['reschedule']=={'allowed':False,'reason':'booking_reschedule_cancelled'}
    assert not any(item.active for item in db.query(VisitItem).filter_by(visit_id=visit.id))


@pytest.mark.parametrize('change',['expire','revoke','retarget'])
def test_same_credential_recheck_after_actual_projection(client,db,action_world,monkeypatch,change):
    w=action_world;headers,sid,deadline=access(db,w);base.grant(db,w);db.commit();observed=[]
    engine=db.get_bind()
    def after(_conn,_cursor,statement,*args):
        if 'authorized_action_visit' in statement and statement.lstrip().startswith('SELECT'):
            observed.append(statement)
            if change=='expire':monkeypatch.setattr(visitor_sessions,'utcnow',lambda:deadline)
            else:
                with Session(engine) as writer:
                    values={'revoked_at':w['now']} if change=='revoke' else {'account_id':w['two']}
                    writer.execute(update(VisitorSession).where(VisitorSession.id==sid).values(**values));writer.commit()
    event.listen(engine,'after_cursor_execute',after)
    try:response=client.get(url(w),headers=headers)
    finally:event.remove(engine,'after_cursor_execute',after)
    assert len(observed)==1;private(response,401)
    assert 'allowed' not in response.text


def test_unpaid_and_unknown_catalog_are_reasons_not_finance_payload(client,db,action_world):
    w=action_world;headers,*_=access(db,w);base.grant(db,w);db.commit()
    db.query(Subscription).filter_by(user_id=w['staff_a']).update({'status':'cancelled'});db.commit()
    response=client.get(url(w),headers=headers);private(response,200)
    assert response.json()=={k:{'allowed':False,'reason':'visitor_actions_unavailable'} for k in ('confirm','cancel','reschedule')}
    db.query(Subscription).filter_by(user_id=w['staff_a']).update({'status':'active'})
    db.get(Service,w['service']).is_active=False;db.commit()
    response=client.get(url(w),headers=headers);private(response,200)
    assert response.json()['confirm']['allowed'] and response.json()['cancel']['allowed']
    assert response.json()['reschedule']=={'allowed':False,'reason':'booking_reschedule_unavailable'}


def test_unexpected_billing_error_keeps_regular_history_readable_and_both_private(db,action_world,monkeypatch):
    from app.main import app
    w=action_world;headers,*_=access(db,w);base.grant(db,w);db.commit()
    def fail(*args,**kwargs):raise RuntimeError('private provider/internal canary')
    monkeypatch.setattr(subscription_access,'enforce_business_access',fail)
    with TestClient(app,raise_server_exceptions=False) as client:
        response=client.get(url(w),headers=headers);private(response,500)
        assert 'private provider' not in response.text
        private(client.get(f"/visitor/visits/{w['a1']}",headers=headers),200)
        private(client.get('/visitor/visits',headers=headers),200)


def test_disabled_slug_and_exact24h_use_explicit_handler_clock(client,db,action_world,monkeypatch):
    w=action_world;headers,*_=access(db,w);base.grant(db,w);db.commit()
    visit=db.get(Visit,w['a1']);visit.date_start=w['now']+timedelta(hours=24)
    visit.date_end=visit.date_start+timedelta(minutes=30)
    db.add(PublicBookingSettings(business_id=w['a'],slug='disabled-action-hint',is_enabled=False));db.commit()
    response=client.get(url(w),headers=headers);private(response,200)
    assert response.json()=={key:{'allowed':True,'reason':None} for key in ('confirm','cancel','reschedule')}
    endpoint=importlib.import_module('app.api.v1.endpoints.visitor_history')
    monkeypatch.setattr(endpoint,'utcnow',lambda:w['now']+timedelta(microseconds=1))
    response=client.get(url(w),headers=headers);private(response,200)
    assert response.json()['confirm']['allowed'] is True
    assert response.json()['cancel']['reason']=='booking_cancel_too_late'
    assert response.json()['reschedule']['reason']=='booking_reschedule_too_late'
