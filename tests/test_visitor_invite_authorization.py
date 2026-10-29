"""Authorization locking probes run against real PostgreSQL, never a live salon."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import delete, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.core import permissions, visitor_invite_authorization as auth
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.client_business_relation import ClientBusinessRelation
from app.db.models.client_specialist_relation import ClientSpecialistRelation
from app.db.models.employee import Employee
from app.db.models.user import User
from app.db.models.visitor_account import VisitorAccount
from app.db.models.visitor_access import VisitorClaim, VisitorGrant
from tests.test_visitor_access_models import seed_access_world


@pytest.fixture
def world(db):
    world = seed_access_world(db)
    db.get(User, world['issuer']).role = 'business_owner'
    db.commit()
    return world


def anchor(db, world):
    db.query(Client.id).filter(Client.id == world['client']).with_for_update(key_share=True).one()


def authorize(db, world, issuer=None):
    return auth.authorize_client_invite(db, issuer_user_id=issuer or world['staff'],
                                       business_id=world['business'], client_id=world['client'])


def add_staff(db, world, active=True, **permissions):
    row = Employee(business_owner_id=world['issuer'], employee_user_id=world['staff'],
                   permissions=permissions or {'can_edit_clients': True}, is_active=active)
    db.add(row)
    db.get(User, world['staff']).employer_id = world['issuer']
    db.commit()
    return row.id


def test_guard_blocks_new_employee_fk_phantom_after_success(db, world):
    anchor(db, world)
    assert authorize(db, world).issuer_user_id == world['staff']
    blocked = False
    with Session(db.get_bind()) as writer:
        writer.execute(text('SET LOCAL lock_timeout=200'))
        writer.add(Employee(business_owner_id=world['issuer'], employee_user_id=world['staff'],
                            permissions={'can_edit_clients': False}, is_active=True))
        try:
            writer.commit()
        except DBAPIError as exc:
            assert exc.orig.pgcode == '55P03'
            writer.rollback()
            blocked = True
    if not blocked:
        assert permissions.can_edit_clients(db.get(User, world['staff']), db) is False
    assert blocked, 'SHARE on User did not prevent a new Employee FK row from narrowing authority'
    db.rollback()


@pytest.mark.parametrize('role', ['admin', 'business_owner', 'specialist', 'employee'])
def test_existing_roles_follow_edit_and_client_helpers_without_new_permissions(db, world, role):
    if role == 'employee':
        add_staff(db, world, can_edit_clients=True, assigned_client_ids=[world['client']])
    db.get(User, world['staff']).role = role
    db.commit()
    anchor(db, world)
    before = {model: db.query(model).count() for model in (VisitorAccount, VisitorClaim, VisitorGrant)}
    result = authorize(db, world)
    assert (result.issuer_user_id, result.business_id, result.client_id) == (
        world['staff'], world['business'], world['client'])
    assert repr(result) == 'ClientInviteAuthority()'
    assert db.in_transaction()
    assert before == {model: db.query(model).count() for model in before}
    db.rollback()


@pytest.mark.parametrize('change', ['flag', 'assigned', 'issuer_deleted', 'owner_deleted',
    'owner_moved', 'issuer_moved', 'business_deleted', 'client_deleted', 'membership_removed',
    'fired', 'admin_no_business', 'unknown_role', 'duplicate_active_employee', 'invalid_permissions'])
def test_fresh_target_and_current_edit_visibility_are_required(db, world, change):
    from datetime import datetime
    staff_id = None
    if change in {'flag', 'assigned', 'owner_deleted', 'owner_moved', 'fired',
                  'duplicate_active_employee', 'invalid_permissions'}:
        staff_id = add_staff(db, world)
    if change == 'flag':
        db.get(Employee, staff_id).permissions = {'can_edit_clients': False}
    elif change == 'assigned':
        db.get(Employee, staff_id).permissions = {'can_edit_clients': True, 'assigned_client_ids': [99999]}
    elif change == 'issuer_deleted':
        db.get(User, world['staff']).deleted_at = datetime(2026, 10, 26)
    elif change == 'owner_deleted':
        db.get(User, world['issuer']).deleted_at = datetime(2026, 10, 26)
    elif change == 'owner_moved':
        db.get(User, world['issuer']).business_id = world['other']
    elif change == 'issuer_moved':
        db.get(User, world['staff']).business_id = world['other']
    elif change == 'business_deleted':
        db.get(Business, world['business']).deleted_at = datetime(2026, 10, 26)
    elif change == 'client_deleted':
        db.get(Client, world['client']).deleted_at = datetime(2026, 10, 26)
    elif change == 'membership_removed':
        db.execute(delete(ClientBusinessRelation).where(ClientBusinessRelation.business_id == world['business']))
    elif change == 'fired':
        db.get(Employee, staff_id).is_active = False
    elif change == 'admin_no_business':
        user = db.get(User, world['staff'])
        user.role, user.business_id = 'admin', None
    elif change == 'unknown_role':
        db.get(User, world['staff']).role = 'visitor'
    elif change == 'duplicate_active_employee':
        db.add(Employee(business_owner_id=world['issuer'], employee_user_id=world['staff'],
                        permissions={'can_edit_clients': True}, is_active=True))
    elif change == 'invalid_permissions':
        db.get(Employee, staff_id).permissions = {'can_edit_clients': 'not a boolean'}
    db.commit()
    anchor(db, world)
    with pytest.raises(auth.InviteAuthorizationDenied):
        authorize(db, world)
    db.rollback()


@pytest.mark.parametrize('assignment', [None, [], 'explicit'])
def test_employee_assigned_client_defaults_match_current_helper(db, world, assignment):
    add_staff(db, world, can_edit_clients=True,
              assigned_client_ids=[world['client']] if assignment == 'explicit' else assignment)
    anchor(db, world)
    assert authorize(db, world).client_id == world['client']
    db.rollback()


def test_solo_specialist_may_use_current_business_link_but_not_foreign_link(db, world):
    db.get(Client, world['client']).owner_id = world['issuer']
    row = ClientSpecialistRelation(client_id=world['client'], specialist_id=world['staff'],
                                   business_id=world['other'])
    db.add(row)
    db.commit()
    anchor(db, world)
    with pytest.raises(auth.InviteAuthorizationDenied):
        authorize(db, world)
    db.rollback()
    db.get(ClientSpecialistRelation, row.id).business_id = world['business']
    db.commit()
    anchor(db, world)
    assert authorize(db, world).issuer_user_id == world['staff']
    db.rollback()


@pytest.mark.parametrize('target', ['user', 'employee', 'owner', 'business', 'membership', 'specialist_link'])
def test_busy_authorization_rows_are_typed_and_require_whole_rollback(db, world, target):
    staff_id = None
    if target in ('employee', 'owner'):
        staff_id = add_staff(db, world)
    if target == 'specialist_link':
        db.get(Client, world['client']).owner_id = world['issuer']
        link = ClientSpecialistRelation(client_id=world['client'], specialist_id=world['staff'],
                                        business_id=world['business'])
        db.add(link)
        db.commit()
        link_id = link.id
    engine = db.get_bind()
    with Session(engine) as writer:
        if target == 'user':
            writer.query(User).filter_by(id=world['staff']).with_for_update().one()
        elif target == 'employee':
            writer.query(Employee).filter_by(id=staff_id).with_for_update().one()
        elif target == 'owner':
            writer.query(User).filter_by(id=world['issuer']).with_for_update().one()
        elif target == 'business':
            writer.query(Business).filter_by(id=world['business']).with_for_update().one()
        elif target == 'membership':
            writer.query(ClientBusinessRelation).filter_by(client_id=world['client'],
                business_id=world['business']).with_for_update().one()
        else:
            writer.query(ClientSpecialistRelation).filter_by(id=link_id).with_for_update().one()
        anchor(db, world)
        with pytest.raises(auth.InviteAuthorizationBusy):
            authorize(db, world)
        with pytest.raises(DBAPIError) as failed_transaction:
            db.execute(text('SELECT 1'))
        assert failed_transaction.value.orig.pgcode == '25P02'
        db.rollback()
        writer.rollback()
    anchor(db, world)
    assert authorize(db, world).client_id == world['client']
    db.rollback()


@pytest.mark.parametrize('active', [False, True])
def test_existing_employee_row_prevents_reactivation_or_permission_narrowing(db, world, active):
    employee_id = add_staff(db, world, active=active)
    if not active:
        db.get(User, world['staff']).employer_id = None
        db.commit()
    anchor(db, world)
    authorize(db, world)
    with Session(db.get_bind()) as writer:
        writer.execute(text('SET LOCAL lock_timeout=200'))
        employee = writer.get(Employee, employee_id)
        employee.is_active = True
        employee.permissions = {'can_edit_clients': False}
        with pytest.raises(DBAPIError) as blocked:
            writer.commit()
        assert blocked.value.orig.pgcode == '55P03'
        writer.rollback()
    db.rollback()


@pytest.mark.parametrize('stale_kind', ['issuer', 'employee', 'owner'])
def test_preloaded_identity_map_cannot_keep_old_scope(db, world, stale_kind):
    from datetime import datetime
    employee_id = add_staff(db, world)
    with Session(db.get_bind(), expire_on_commit=False) as caller:
        loaded_issuer = caller.get(User, world['staff'])
        loaded_owner = caller.get(User, world['issuer'])
        loaded_employee = caller.get(Employee, employee_id)
        assert permissions.can_edit_clients(loaded_issuer, caller)
        caller.commit()
        with Session(db.get_bind()) as writer:
            if stale_kind == 'issuer':
                writer.get(User, world['staff']).deleted_at = datetime(2026, 10, 26)
            elif stale_kind == 'employee':
                writer.get(Employee, employee_id).permissions = {'can_edit_clients': False}
            else:
                writer.get(User, world['issuer']).business_id = world['other']
            writer.commit()
        assert loaded_issuer.deleted_at is None and loaded_employee.permissions['can_edit_clients']
        assert loaded_owner.business_id == world['business']
        anchor(caller, world)
        with pytest.raises(auth.InviteAuthorizationDenied):
            authorize(caller, world)
        caller.rollback()


def test_no_auth_dependency_or_payment_gate_is_called(db, world, monkeypatch):
    from app.api import deps
    from app.api.core import subscription_access
    for name in ('enforce_business_access', 'enforce_subscription_request_access', 'enforce_subscription_mutation_access'):
        monkeypatch.setattr(subscription_access, name, lambda *a, **k: pytest.fail('accept checked CRM payment'))
    monkeypatch.setattr(deps, 'get_current_user', lambda *a, **k: pytest.fail('visitor accept authenticated CRM'))
    anchor(db, world)
    assert authorize(db, world).client_id == world['client']
    db.rollback()


def test_real_user_first_client_wait_does_not_deadlock_visitor_client_then_user_nowait(db, world):
    import time
    user_held, client_held = Event(), Event()
    engine, shared = db.get_bind(), {}
    def crm_writer():
        with Session(engine) as writer:
            writer.execute(text('SET LOCAL lock_timeout=5000'))
            shared['crm_pid'] = writer.execute(text('SELECT pg_backend_pid()')).scalar_one()
            writer.query(User.id).filter_by(id=world['staff']).with_for_update().one()
            user_held.set()
            assert client_held.wait(5)
            anchor(writer, world)
            writer.commit()
            return 'crm committed'
    def visitor_accept():
        with Session(engine) as caller:
            caller.execute(text('SET LOCAL lock_timeout=5000'))
            assert user_held.wait(5)
            caller.query(VisitorAccount.id).filter_by(id=world['one']).with_for_update().one()
            anchor(caller, world)
            visitor_pid = caller.execute(text('SELECT pg_backend_pid()')).scalar_one()
            client_held.set()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if caller.execute(text('SELECT :visitor = ANY(pg_blocking_pids(:crm))'),
                                  {'visitor': visitor_pid, 'crm': shared['crm_pid']}).scalar_one():
                    break
                time.sleep(.01)
            else:
                caller.rollback()
                pytest.fail('CRM writer never actually waited on the visitor Client anchor')
            with pytest.raises(auth.InviteAuthorizationBusy):
                authorize(caller, world)
            caller.rollback()
            return 'visitor retried'
    with ThreadPoolExecutor(max_workers=2) as pool:
        crm = pool.submit(crm_writer)
        visitor = pool.submit(visitor_accept)
        assert visitor.result(timeout=7) == 'visitor retried'
        assert crm.result(timeout=7) == 'crm committed'
