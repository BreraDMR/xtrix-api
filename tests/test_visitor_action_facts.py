from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import event, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.core import subscription_access
from app.api.core.clock import utcnow
from app.api.core.visitor_action_facts import AuthorizedActionTarget, ServiceMasterPair, read_booking_manage_eligibility
from app.db.models.business import Business
from app.db.models.employee import Employee
from app.db.models.public_booking import PublicBookingSettings
from app.db.models.service import Service
from app.db.models.service_specialist import ServiceSpecialist
from app.db.models.subscription import Subscription
from app.db.models.subscription_plan import SubscriptionPlan
from app.db.models.user import User


@pytest.fixture
def world(db):
    now = utcnow()
    business = Business(name="Private target business", business_type="salon")
    db.add(business); db.flush()
    owner = User(full_name="Owner", email="facts-owner@example.com", role="business_owner", business_id=business.id,
                 hashed_password="not-used")
    master = User(full_name="Master", email="facts-master@example.com", role="specialist", hashed_password="not-used")
    unrelated = User(full_name="Original", email="facts-other@example.com", role="specialist", hashed_password="not-used")
    db.add_all([owner, master, unrelated]); db.flush()
    db.add(Employee(business_owner_id=owner.id, employee_user_id=master.id, is_active=True))
    plan = db.query(SubscriptionPlan).first()
    sub = Subscription(user_id=owner.id, plan_id=plan.id, status="active", current_period_end=now + timedelta(days=5))
    service = Service(name="Stored service", specialist_id=owner.id, business_id=business.id,
                      duration_minutes=30, is_active=True, price=19)
    db.add_all([sub, service]); db.flush()
    db.add(ServiceSpecialist(service_id=service.id, specialist_id=master.id))
    db.commit()
    return dict(now=now, business=business, owner=owner, master=master, unrelated=unrelated, sub=sub, service=service)


def target(w, pairs=None, **changes):
    values = dict(visit_id=987654321, business_id=w['business'].id, status="planned",
                  start=w['now'] + timedelta(days=2),
                  service_master_pairs=(ServiceMasterPair(w['service'].id,w['master'].id),) if pairs is None else pairs)
    values.update(changes)
    return AuthorizedActionTarget(**values)


def read(db, w, **changes):
    return read_booking_manage_eligibility(db, target(w, **changes), now=w['now'])


def test_real_readonly_with_dirty_object_has_only_select_and_preserves_caller_transaction(db, world):
    w=world
    current=target(w)
    unrelated=w['unrelated']; unrelated.full_name="Pending caller edit"
    conn=db.connection()
    conn.execute(text('SET TRANSACTION READ ONLY'))
    statements=[]
    def record(conn, cursor, statement, parameters, context, many):
        statements.append(statement)
    event.listen(conn, 'before_cursor_execute', record)
    try:
        result=read_booking_manage_eligibility(db,current,now=w['now'])
        assert result.confirm.allowed and result.cancel.allowed and result.reschedule.allowed
        assert statements and all(s.lstrip().upper().startswith('SELECT') for s in statements)
        assert unrelated.full_name=="Pending caller edit" and unrelated in db.dirty
        assert conn.in_transaction()
        assert conn.execute(text("SHOW transaction_read_only")).scalar()=='on'
    finally:
        event.remove(conn, 'before_cursor_execute', record)
    db.rollback()
    assert db.get(User, unrelated.id).full_name=="Original"


@pytest.mark.parametrize('mode',['expired','no_subscription','ai_only'])
def test_unentitled_business_disables_all_without_billing_details(db,world,mode):
    sub=world['sub']
    if mode=='expired': sub.current_period_end=utcnow()-timedelta(seconds=1)
    elif mode=='no_subscription': db.delete(sub)
    else: sub.offer_code='ai_pro_czk'
    db.commit()
    result=read(db,world)
    assert {r.reason for r in (result.confirm,result.cancel,result.reschedule)}=={'visitor_actions_unavailable'}


def test_disabled_public_slug_does_not_disable_existing_management(db,world):
    db.add(PublicBookingSettings(business_id=world['business'].id,slug='disabled-facts',is_enabled=False));db.commit()
    result=read(db,world)
    assert result.confirm.allowed and result.cancel.allowed and result.reschedule.allowed


@pytest.mark.parametrize('pairs',['empty','legacy_unknown','foreign_master','missing_service'])
def test_unknown_current_catalog_only_blocks_reschedule(db,world,pairs):
    w=world
    values={'empty':(), 'legacy_unknown':(ServiceMasterPair(None,w['master'].id),),
            'foreign_master':(ServiceMasterPair(w['service'].id,w['unrelated'].id),),
            'missing_service':(ServiceMasterPair(999999,w['master'].id),)}
    result=read(db,w,pairs=values[pairs])
    assert result.confirm.allowed and result.cancel.allowed
    assert result.reschedule.reason=='booking_reschedule_unavailable'


def test_all_ordered_masters_are_checked(db,world):
    w=world
    good=ServiceMasterPair(w['service'].id,w['owner'].id)
    other=ServiceMasterPair(w['service'].id,w['master'].id)
    assert read(db,w,pairs=(good,other)).reschedule.allowed
    result=read(db,w,pairs=(good,other,ServiceMasterPair(w['service'].id,w['unrelated'].id)))
    assert not result.reschedule.allowed


@pytest.mark.parametrize('change',['service_deleted','service_inactive','master_archived','staff_fired','link_removed'])
def test_fresh_catalog_ignores_stale_or_dirty_caller_identity_without_refreshing_it(db,world,change):
    w=world; current=target(w)
    assert read(db,w).reschedule.allowed
    # Keep caller instances live and dirty; the adapter must not refresh/discard them.
    service=w['service']; service.name='Pending service name'
    conn=db.connection()
    with Session(bind=conn.engine) as writer:
        if change=='service_deleted': writer.execute(update(Service).where(Service.id==service.id).values(deleted_at=utcnow()))
        elif change=='service_inactive': writer.execute(update(Service).where(Service.id==service.id).values(is_active=False))
        elif change=='master_archived': writer.execute(update(User).where(User.id==w['master'].id).values(archived_at=utcnow()))
        elif change=='staff_fired': writer.execute(update(Employee).where(Employee.employee_user_id==w['master'].id).values(is_active=False))
        else:
            writer.query(ServiceSpecialist).filter_by(service_id=service.id,specialist_id=w['master'].id).delete()
        writer.commit()
    result=read_booking_manage_eligibility(db,current,now=w['now'])
    assert result.confirm.allowed and result.cancel.allowed and not result.reschedule.allowed
    assert service.name=='Pending service name' and service in db.dirty


def test_fresh_billing_does_not_reuse_caller_stale_subscription(db,world):
    w=world; current=target(w); sub=w['sub']; assert sub.status=='active'
    with Session(bind=db.get_bind()) as writer:
        writer.execute(update(Subscription).where(Subscription.id==sub.id).values(status='cancelled'));writer.commit()
    sub.provider='Pending provider note'
    result=read_booking_manage_eligibility(db,current,now=w['now'])
    assert not result.confirm.allowed
    assert sub.status=='active' and sub.provider=='Pending provider note' and sub in db.dirty


@pytest.mark.parametrize('code',[402,403,404,500])
def test_only_expected_billing_denials_are_mapped(db,world,monkeypatch,code):
    def deny(*args,**kwargs):raise HTTPException(code,detail='private billing details')
    monkeypatch.setattr(subscription_access,'enforce_business_access',deny)
    if code in (402,403): assert read(db,world).confirm.reason=='visitor_actions_unavailable'
    else:
        with pytest.raises(HTTPException) as exc: read(db,world)
        assert exc.value.status_code==code


def test_unexpected_database_error_is_not_hidden(db,world,monkeypatch):
    error=DBAPIError('SELECT',{},RuntimeError('database unavailable'),False)
    def fail(*args,**kwargs):raise error
    monkeypatch.setattr(subscription_access,'enforce_business_access',fail)
    with pytest.raises(DBAPIError) as exc: read(db,world)
    assert exc.value is error


def test_borrowed_close_does_not_commit_or_rollback_parent_and_parent_can_commit(db,world):
    w=world; current=target(w)
    transient=Business(name='Uncommitted parent insert',business_type='salon')
    db.add(transient);db.flush()
    transient_id=transient.id
    other=w['unrelated']; other_id=other.id; other.full_name='Pending parent edit'
    assert read_booking_manage_eligibility(db,current,now=w['now']).reschedule.allowed
    assert transient in db and other in db.dirty
    assert other.full_name=='Pending parent edit'
    with Session(bind=db.get_bind()) as observer:
        assert observer.get(Business,transient_id) is None
        assert observer.get(User,other_id).full_name=='Original'
    db.commit()
    with Session(bind=db.get_bind()) as observer:
        assert observer.get(Business,transient_id).name=='Uncommitted parent insert'
        assert observer.get(User,other_id).full_name=='Pending parent edit'


def test_unexpected_error_closes_only_borrowed_session_without_rolling_back_parent(db,world,monkeypatch):
    pending=Business(name='Retained parent insert',business_type='salon');db.add(pending);db.flush()
    pending_id=pending.id
    def fail(*args,**kwargs):raise RuntimeError('Unexpected failure')
    monkeypatch.setattr(subscription_access,'enforce_business_access',fail)
    with pytest.raises(RuntimeError):read(db,world)
    db.commit()
    with Session(bind=db.get_bind()) as observer:
        assert observer.get(Business,pending_id).name=='Retained parent insert'


def test_expired_trial_read_only_window_does_not_authorize_actions(db,world):
    sub=world['sub'];sub.is_trial=True
    sub.trial_end=utcnow()-timedelta(hours=1)
    sub.current_period_end=utcnow()+timedelta(days=5)
    db.commit()
    result=read(db,world)
    assert not result.confirm.allowed and not result.cancel.allowed and not result.reschedule.allowed


def test_actual_pg_error_propagates_and_caller_owns_failed_transaction_recovery(db,world,monkeypatch):
    def fail(reads,*args,**kwargs):reads.execute(text('SELECT 1/0'))
    monkeypatch.setattr(subscription_access,'enforce_business_access',fail)
    with pytest.raises(DBAPIError) as exc:read(db,world)
    assert exc.value.orig.pgcode=='22012'
    # Closing the borrowed session must not disguise or clear the outer PG failure.
    with pytest.raises(DBAPIError) as failed:db.execute(text('SELECT 1'))
    assert failed.value.orig.pgcode=='25P02'
    db.rollback()
    assert db.execute(text('SELECT 1')).scalar()==1
