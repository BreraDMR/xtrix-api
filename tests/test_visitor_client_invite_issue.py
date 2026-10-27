"""Staff invitation issuance uses current permissions and CRM payment, never email matching."""
from datetime import timedelta
import hashlib
import importlib

import pytest
from sqlalchemy.orm import Session

from app.api.core import subscription_access
from app.db.models.subscription import Subscription
from app.db.models.subscription_plan import SubscriptionPlan
from app.db.models.visitor_access import VisitorClaim
from tests.test_visitor_access_models import NOW, seed_access_world


@pytest.fixture
def world(db, monkeypatch):
    world = seed_access_world(db)
    monkeypatch.setattr(subscription_access, 'utcnow', lambda: NOW)
    plan = SubscriptionPlan(code='invite_test_crm', name='Synthetic CRM', period_days=30, price=200)
    db.add(plan)
    db.flush()
    sub = Subscription(user_id=world['staff'], plan_id=plan.id, status='active',
                       current_period_end=NOW + timedelta(days=30), is_trial=False)
    db.add(sub)
    db.flush()
    world.update(subscription=sub.id, plan=plan.id)
    db.commit()
    return world


def core(monkeypatch):
    module = importlib.import_module('app.api.core.visitor_client_invite_issue')
    monkeypatch.setattr(module, 'utcnow', lambda: NOW)
    return module


def test_issue_flushes_only_hash_bound_scope_without_committing(db, world, monkeypatch):
    api = core(monkeypatch)
    result = api.issue_client_invite(db, world['staff'], world['client'], '  New.Recipient+Tag@EXAMPLE.COM  ')
    assert result.raw_token.startswith('vci_') and len(result.raw_token) == 47
    assert result.raw_token not in repr(result)
    assert result.expires_at == NOW + timedelta(minutes=30)
    assert db.in_transaction()
    with Session(db.get_bind()) as observer:
        assert observer.query(VisitorClaim).count() == 0
    db.commit()
    proof = db.get(VisitorClaim, result.claim_id)
    assert proof.kind == 'client_invite' and proof.visit_id is None and proof.source_manage_token_id is None
    assert (proof.business_id, proof.client_id, proof.issued_by_user_id) == (
        world['business'], world['client'], world['staff'])
    assert proof.recipient_email == 'new.recipient+tag@example.com'
    assert proof.proof_hash == hashlib.sha256(result.raw_token.encode()).hexdigest()
    assert proof.created_at == NOW and proof.expires_at == result.expires_at
    assert proof.consumed_at is None and proof.grant_id is None and proof.consumed_account_id is None


def test_no_subscription_cannot_issue_full_history_invite(db, world, monkeypatch):
    from fastapi import HTTPException
    api = core(monkeypatch)
    db.delete(db.get(Subscription, world['subscription']))
    db.commit()
    with pytest.raises(HTTPException) as denied:
        api.issue_client_invite(db, world['staff'], world['client'], 'recipient@example.com')
    assert denied.value.status_code == 402
    db.rollback()
    assert db.query(VisitorClaim).count() == 0


@pytest.mark.parametrize('email', ['', 'bad-address', 'a b@example.com', None, 12, 'x' * 321 + '@example.com'])
def test_invalid_recipient_does_not_write_proof(db, world, monkeypatch, email):
    api = core(monkeypatch)
    with pytest.raises(api.InvalidInviteRecipient) as invalid:
        api.issue_client_invite(db, world['staff'], world['client'], email)
    assert 'example.com' not in str(invalid.value)
    db.rollback()
    assert db.query(VisitorClaim).count() == 0


@pytest.mark.parametrize('email', ['owner.one@example.com', 'unknown@example.com'])
def test_recipient_does_not_trigger_any_account_identity_lookup(db, world, monkeypatch, email):
    from sqlalchemy import event
    api = core(monkeypatch)
    def observe(conn, cursor, statement, parameters, context, executemany):
        assert 'from visitor_accounts' not in statement.lower()
        assert 'from visitor_google_identities' not in statement.lower()
    engine = db.get_bind()
    event.listen(engine, 'before_cursor_execute', observe)
    try:
        prepared = api.issue_client_invite(db, world['staff'], world['client'], email)
    finally:
        event.remove(engine, 'before_cursor_execute', observe)
    db.commit()
    assert db.get(VisitorClaim, prepared.claim_id).recipient_email == email


def test_only_new_invite_changes_across_all_existing_tables(db, world, monkeypatch):
    import json
    from sqlalchemy import MetaData, select
    api = core(monkeypatch)
    metadata = MetaData()
    metadata.reflect(bind=db.get_bind())
    def rows():
        with db.get_bind().connect() as observer:
            return {table.name: sorted(json.dumps(dict(row), sort_keys=True, default=str)
                    for row in observer.execute(select(table)).mappings())
                    for table in metadata.sorted_tables if table.name != 'visitor_claims'}
    before = rows()
    api.issue_client_invite(db, world['staff'], world['client'], 'recipient@example.com')
    db.commit()
    assert rows() == before
    assert db.query(VisitorClaim).count() == 1


@pytest.mark.parametrize('denied', ['edit_flag', 'assigned', 'foreign_business', 'deleted_client', 'no_business'])
def test_existing_permissions_and_fixed_current_business_scope_are_required(db, world, monkeypatch, denied):
    from app.api.core.visitor_invite_authorization import InviteAuthorizationDenied
    from app.db.models.client import Client
    from app.db.models.client_business_relation import ClientBusinessRelation
    from app.db.models.employee import Employee
    from app.db.models.user import User
    from sqlalchemy import delete
    api = core(monkeypatch)
    if denied in ('edit_flag', 'assigned'):
        db.get(User, world['issuer']).role = 'business_owner'
        db.get(User, world['staff']).employer_id = world['issuer']
        db.add(Employee(business_owner_id=world['issuer'], employee_user_id=world['staff'], is_active=True,
            permissions={'can_edit_clients': denied != 'edit_flag',
                         'assigned_client_ids': [999999] if denied == 'assigned' else None}))
    elif denied == 'foreign_business':
        db.get(User, world['staff']).business_id = world['other']
        db.execute(delete(ClientBusinessRelation).where(ClientBusinessRelation.business_id == world['other']))
    elif denied == 'deleted_client':
        db.get(Client, world['client']).deleted_at = NOW
    else:
        db.get(User, world['staff']).business_id = None
    db.commit()
    with pytest.raises(InviteAuthorizationDenied):
        api.issue_client_invite(db, world['staff'], world['client'], 'recipient@example.com')
    db.rollback()
    assert db.query(VisitorClaim).count() == 0


@pytest.mark.parametrize('owner_status', ['active', 'cancelled'])
def test_employee_billing_uses_actual_owner_payer_not_staff_subscription(db, world, monkeypatch, owner_status):
    from app.db.models.employee import Employee
    from app.db.models.user import User
    from fastapi import HTTPException
    api = core(monkeypatch)
    db.get(User, world['issuer']).role = 'business_owner'
    db.get(User, world['staff']).employer_id = world['issuer']
    db.add(Employee(business_owner_id=world['issuer'], employee_user_id=world['staff'],
                    is_active=True, permissions={'can_edit_clients': True}))
    db.add(Subscription(user_id=world['issuer'], plan_id=world['plan'], status=owner_status,
                        current_period_end=NOW + timedelta(days=30), is_trial=False))
    db.commit()
    if owner_status == 'active':
        assert api.issue_client_invite(db, world['staff'], world['client'], 'recipient@example.com').claim_id
        db.commit()
    else:
        with pytest.raises(HTTPException) as denied:
            api.issue_client_invite(db, world['staff'], world['client'], 'recipient@example.com')
        assert denied.value.status_code == 402
        db.rollback()
        assert db.query(VisitorClaim).count() == 0


@pytest.mark.parametrize('change', ['cancel', 'expire', 'trial_expire'])
def test_preloaded_subscription_cannot_keep_stale_mutation_access(db, world, monkeypatch, change):
    from fastapi import HTTPException
    api = core(monkeypatch)
    with Session(db.get_bind(), expire_on_commit=False) as caller:
        stale = caller.get(Subscription, world['subscription'])
        caller.commit()
        with Session(db.get_bind()) as writer:
            row = writer.get(Subscription, world['subscription'])
            if change == 'cancel':
                row.status = 'cancelled'
            elif change == 'expire':
                row.current_period_end = NOW
            else:
                row.is_trial, row.trial_end = True, NOW
            writer.commit()
        assert stale.status == 'active' and stale.current_period_end > NOW and stale.is_trial is False
        with pytest.raises(HTTPException) as denied:
            api.issue_client_invite(caller, world['staff'], world['client'], 'recipient@example.com')
        assert denied.value.status_code == 402
        caller.rollback()
    assert db.query(VisitorClaim).count() == 0


def test_rollback_and_actual_deferred_commit_failure_leave_no_public_proof(db, world, monkeypatch):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    api = core(monkeypatch)
    prepared = api.issue_client_invite(db, world['staff'], world['client'], 'recipient@example.com')
    assert prepared.raw_token not in repr(prepared)
    db.rollback()
    assert db.query(VisitorClaim).count() == 0
    api.issue_client_invite(db, world['staff'], world['client'], 'recipient@example.com')
    db.execute(text('CREATE TEMP TABLE invite_commit_failure (n integer UNIQUE DEFERRABLE INITIALLY DEFERRED)'))
    db.execute(text('INSERT INTO invite_commit_failure VALUES(1),(1)'))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    assert db.query(VisitorClaim).count() == 0
    api.issue_client_invite(db, world['staff'], world['client'], 'recipient@example.com')
    db.commit()
    assert db.query(VisitorClaim).count() == 1


def test_fresh_distinct_invites_never_consume_or_revoke_earlier_invite(db, world, monkeypatch):
    api = core(monkeypatch)
    one = api.issue_client_invite(db, world['staff'], world['client'], 'one@example.com')
    two = api.issue_client_invite(db, world['staff'], world['client'], 'two@example.com')
    db.commit()
    assert one.raw_token != two.raw_token and one.claim_id != two.claim_id
    assert all(proof.consumed_at is None and proof.revoked_at is None for proof in db.query(VisitorClaim))


def _wait_for_real_block(observer, waiting_pid, blocking_pid):
    import time
    from sqlalchemy import text
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if observer.execute(text('SELECT :blocker = ANY(pg_blocking_pids(:waiting))'),
                            {'blocker': blocking_pid, 'waiting': waiting_pid}).scalar_one():
            return
        time.sleep(.01)
    raise AssertionError('Expected real PostgreSQL lock wait was not observed')


@pytest.mark.parametrize('change', ['user_trial', 'client_trial', 'client_cancel', 'client_permission', 'user_business'])
def test_waiting_user_or_client_lock_rechecks_time_billing_and_scope(db, world, monkeypatch, change):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from fastapi import HTTPException
    from sqlalchemy import delete, text
    from app.api.core.visitor_invite_authorization import InviteAuthorizationDenied
    from app.db.models.client import Client
    from app.db.models.client_business_relation import ClientBusinessRelation
    from app.db.models.employee import Employee
    from app.db.models.user import User
    api = core(monkeypatch)
    clock = [NOW]
    monkeypatch.setattr(api, 'utcnow', lambda: clock[0])
    monkeypatch.setattr(subscription_access, 'utcnow', lambda: clock[0])
    if change.endswith('trial'):
        sub = db.get(Subscription, world['subscription'])
        sub.is_trial, sub.trial_end = True, NOW + timedelta(minutes=1)
    if change == 'client_permission':
        db.get(User, world['issuer']).role = 'business_owner'
        db.get(User, world['staff']).employer_id = world['issuer']
        employee = Employee(business_owner_id=world['issuer'], employee_user_id=world['staff'],
                            permissions={'can_edit_clients': True}, is_active=True)
        db.add(employee)
        db.add(Subscription(user_id=world['issuer'], plan_id=world['plan'], status='active',
                            current_period_end=NOW + timedelta(days=30), is_trial=False))
        db.flush()
        employee_id = employee.id
    if change == 'user_business':
        db.execute(delete(ClientBusinessRelation).where(ClientBusinessRelation.business_id == world['other']))
    db.commit()
    ready, shared, engine = Event(), {}, db.get_bind()
    with Session(engine) as holder:
        holder.execute(text('SET LOCAL lock_timeout=2000'))
        holder_pid = holder.execute(text('SELECT pg_backend_pid()')).scalar_one()
        if change.startswith('user'):
            holder.query(User.id).filter_by(id=world['staff']).with_for_update().one()
        else:
            holder.query(Client.id).filter_by(id=world['client']).with_for_update(key_share=True).one()
        def attempt():
            with Session(engine) as worker:
                worker.execute(text('SET LOCAL lock_timeout=5000'))
                shared['worker_pid'] = worker.execute(text('SELECT pg_backend_pid()')).scalar_one()
                cached_subscription = worker.get(Subscription, world['subscription'])
                assert cached_subscription.status == 'active'
                ready.set()
                try:
                    api.issue_client_invite(worker, world['staff'], world['client'], 'recipient@example.com')
                    worker.commit()
                    return 'unexpected success'
                except HTTPException as error:
                    worker.rollback()
                    return error.status_code
                except InviteAuthorizationDenied:
                    worker.rollback()
                    return 'denied'
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(attempt)
            assert ready.wait(5)
            try:
                _wait_for_real_block(holder, shared['worker_pid'], holder_pid)
                if change.endswith('trial'):
                    clock[0] = NOW + timedelta(minutes=2)
                elif change == 'client_cancel':
                    holder.get(Subscription, world['subscription']).status = 'cancelled'
                elif change == 'client_permission':
                    holder.get(Employee, employee_id).permissions = {'can_edit_clients': False}
                else:
                    holder.get(User, world['staff']).business_id = world['other']
                holder.commit()
                assert future.result(timeout=5) == ('denied' if change in ('client_permission', 'user_business') else 402)
            finally:
                holder.rollback()
    assert db.query(VisitorClaim).count() == 0


@pytest.mark.parametrize('change', ['ttl', 'cancel', 'trial', 'unique_conflict'])
def test_unique_insert_wait_rechecks_ttl_and_fresh_subscription_after_last_write(db, world, monkeypatch, change):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from fastapi import HTTPException
    from sqlalchemy import text
    api = core(monkeypatch)
    clock = [NOW]
    monkeypatch.setattr(api, 'utcnow', lambda: clock[0])
    monkeypatch.setattr(subscription_access, 'utcnow', lambda: clock[0])
    monkeypatch.setattr(api.secrets, 'token_urlsafe', lambda size: 'u' * 43)
    raw = 'vci_' + 'u' * 43
    digest = hashlib.sha256(raw.encode()).hexdigest()
    if change == 'trial':
        sub = db.get(Subscription, world['subscription'])
        sub.is_trial, sub.trial_end = True, NOW + timedelta(minutes=1)
        db.commit()
    ready, shared, engine = Event(), {}, db.get_bind()
    with Session(engine) as holder:
        holder.execute(text('SET LOCAL lock_timeout=2000'))
        holder_pid = holder.execute(text('SELECT pg_backend_pid()')).scalar_one()
        holder.add(VisitorClaim(kind='client_invite', proof_hash=digest, business_id=world['business'],
            client_id=world['client'], recipient_email='competing@example.com', issued_by_user_id=world['issuer'],
            created_at=NOW, expires_at=NOW + timedelta(minutes=30)))
        holder.flush()
        def attempt():
            with Session(engine) as worker:
                worker.execute(text('SET LOCAL lock_timeout=5000'))
                shared['worker_pid'] = worker.execute(text('SELECT pg_backend_pid()')).scalar_one()
                cached_subscription = worker.get(Subscription, world['subscription'])
                assert cached_subscription.status == 'active'
                ready.set()
                try:
                    api.issue_client_invite(worker, world['staff'], world['client'], 'recipient@example.com')
                    worker.commit()
                    return 'unexpected success'
                except api.InviteIssueUnavailable:
                    worker.rollback()
                    return 'unavailable'
                except HTTPException as error:
                    worker.rollback()
                    return error.status_code
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(attempt)
            assert ready.wait(5)
            try:
                _wait_for_real_block(holder, shared['worker_pid'], holder_pid)
                if change == 'ttl':
                    clock[0] = NOW + timedelta(minutes=31)
                elif change == 'trial':
                    clock[0] = NOW + timedelta(minutes=2)
                elif change == 'cancel':
                    with Session(engine) as writer:
                        writer.execute(text('SET LOCAL lock_timeout=1000'))
                        writer.get(Subscription, world['subscription']).status = 'cancelled'
                        writer.commit()
                if change == 'unique_conflict':
                    holder.commit()
                else:
                    holder.rollback()
                assert future.result(timeout=5) == ('unavailable' if change in ('ttl', 'unique_conflict') else 402)
            finally:
                holder.rollback()
    assert db.query(VisitorClaim).count() == (1 if change == 'unique_conflict' else 0)
