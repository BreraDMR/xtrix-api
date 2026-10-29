"""Guest links grant exactly one visit, with rollback-safe receipt ownership."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib
import importlib
from threading import Event

import pytest
from sqlalchemy import delete, event, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.core import booking_manage_access, visitor_sessions
from app.db.models.booking_manage_token import BookingManageToken
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.client_business_relation import ClientBusinessRelation
from app.db.models.visit import Visit
from app.db.models.visitor_access import VisitorClaim, VisitorGrant
from app.db.models.visitor_auth import VisitorSession
from tests.test_visitor_access_models import NOW, seed_access_world, grant_data

RAW_MANAGE = 'synthetic-manage-proof-with-own-digest'


@pytest.fixture
def world(db, monkeypatch):
    state = seed_access_world(db)
    monkeypatch.setattr(visitor_sessions, 'utcnow', lambda: NOW)
    source = db.get(BookingManageToken, state['token'])
    source.token_hash = hashlib.sha256(RAW_MANAGE.encode()).hexdigest()
    db.commit()
    one = visitor_sessions.lock_verified_account(db, state['one'])
    _, state['session_one'] = visitor_sessions.create_session(db, one)
    db.commit()
    two = visitor_sessions.lock_verified_account(db, state['two'])
    _, state['session_two'] = visitor_sessions.create_session(db, two)
    db.commit()
    return state


def core(monkeypatch):
    module = importlib.import_module('app.api.core.visitor_manage_claim')
    monkeypatch.setattr(module, 'utcnow', lambda: NOW)
    return module


def claim(db, world, monkeypatch, **changes):
    api = core(monkeypatch)
    return api.consume_guest_manage(db, changes.get('session', world['session_one']),
                                   changes.get('manage', RAW_MANAGE))


def test_guest_manage_claim_creates_one_visit_grant_and_preserves_legacy_source(db, world, monkeypatch):
    api = core(monkeypatch)
    before_source = {column.name: getattr(db.get(BookingManageToken, world['token']), column.name)
                     for column in BookingManageToken.__table__.columns}
    for name in ('resolve_token', 'issue_token', 'revoke_token'):
        monkeypatch.setattr(booking_manage_access, name, lambda *a, **k: pytest.fail('legacy commit/payment path'))
    result = claim(db, world, monkeypatch)
    assert result.created is True and result.account_id == world['one']
    assert (result.business_id, result.client_id, result.visit_id) == (world['business'], world['client'], world['visit'])
    assert 'vst_' not in repr(result) and RAW_MANAGE not in repr(result)
    assert db.in_transaction()
    with Session(db.get_bind()) as observer:
        assert observer.query(VisitorGrant).count() == observer.query(VisitorClaim).count() == 0
    db.commit()
    grant = db.get(VisitorGrant, result.grant_id)
    assert grant.scope == 'visit' and grant.revoked_at is None
    receipt = db.query(VisitorClaim).one()
    assert receipt.kind == 'manage_visit' and receipt.consumed_at == NOW
    assert receipt.consumed_account_id == world['one'] and receipt.grant_id == grant.id
    assert receipt.source_manage_token_id == world['token'] and receipt.recipient_email is None
    assert receipt.proof_hash == hashlib.sha256(RAW_MANAGE.encode()).hexdigest()
    assert before_source == {column.name: getattr(db.get(BookingManageToken, world['token']), column.name)
                            for column in BookingManageToken.__table__.columns}


def test_same_account_repeat_returns_only_original_active_grant(db, world, monkeypatch):
    first = claim(db, world, monkeypatch)
    db.commit()
    repeated = claim(db, world, monkeypatch)
    db.commit()
    assert repeated.created is False and repeated.grant_id == first.grant_id
    assert db.query(VisitorGrant).count() == db.query(VisitorClaim).count() == 1


@pytest.mark.parametrize('manage', [None, '', 'x' * 129, 'unknown', 123, '\ud800'])
def test_malformed_or_unknown_manage_proof_cannot_grant_history(db, world, monkeypatch, manage):
    api = core(monkeypatch)
    with pytest.raises(api.InvalidManageClaim):
        claim(db, world, monkeypatch, manage=manage)
    db.rollback()
    assert db.query(VisitorGrant).count() == db.query(VisitorClaim).count() == 0


@pytest.mark.parametrize('session', [None, '', 'Bearer crm-token', RAW_MANAGE])
def test_only_current_visitor_namespace_authenticates(db, world, monkeypatch, session):
    api = core(monkeypatch)
    with pytest.raises(api.InvalidVisitorSession):
        claim(db, world, monkeypatch, session=session)
    db.rollback()
    assert db.query(VisitorGrant).count() == 0


@pytest.mark.parametrize('field,value', [('expires_at', NOW), ('revoked_at', NOW)])
def test_first_consume_requires_live_source(db, world, monkeypatch, field, value):
    api = core(monkeypatch)
    setattr(db.get(BookingManageToken, world['token']), field, value)
    db.commit()
    with pytest.raises(api.InvalidManageClaim):
        claim(db, world, monkeypatch)
    db.rollback()
    assert db.query(VisitorGrant).count() == db.query(VisitorClaim).count() == 0


@pytest.mark.parametrize('source_state', ['expired', 'revoked', 'deleted'])
def test_spent_same_account_repeat_does_not_depend_on_old_source_lifetime(db, world, monkeypatch, source_state):
    first = claim(db, world, monkeypatch)
    db.commit()
    if source_state == 'deleted':
        db.execute(delete(BookingManageToken).where(BookingManageToken.id == world['token']))
    elif source_state == 'expired':
        db.get(BookingManageToken, world['token']).expires_at = NOW
    else:
        db.get(BookingManageToken, world['token']).revoked_at = NOW
    db.commit()
    repeated = claim(db, world, monkeypatch)
    db.commit()
    assert repeated.grant_id == first.grant_id and repeated.created is False


def test_foreign_account_cannot_replay_receipt(db, world, monkeypatch):
    api = core(monkeypatch)
    first = claim(db, world, monkeypatch)
    db.commit()
    with pytest.raises(api.ManageClaimConflict):
        claim(db, world, monkeypatch, session=world['session_two'])
    db.rollback()
    assert db.get(VisitorGrant, first.grant_id).account_id == world['one']


@pytest.mark.parametrize('change', ['revoke_grant', 'delete_grant', 'revoke_receipt', 'replace_grant'])
def test_spent_proof_never_revives_or_switches_original_grant(db, world, monkeypatch, change):
    api = core(monkeypatch)
    first = claim(db, world, monkeypatch)
    db.commit()
    if change == 'revoke_receipt':
        db.query(VisitorClaim).one().revoked_at = NOW
    elif change == 'delete_grant':
        db.execute(delete(VisitorGrant).where(VisitorGrant.id == first.grant_id))
    else:
        db.get(VisitorGrant, first.grant_id).revoked_at = NOW
        db.flush()
        if change == 'replace_grant':
            db.add(VisitorGrant(**grant_data(world)))
    db.commit()
    with pytest.raises(api.InvalidManageClaim):
        claim(db, world, monkeypatch)
    db.rollback()
    assert db.query(VisitorClaim).one().consumed_at == NOW


@pytest.mark.parametrize('scope,owner', [('client', 'two'), ('visit', 'two')])
def test_known_foreign_target_grant_prevents_new_claim(db, world, monkeypatch, scope, owner):
    api = core(monkeypatch)
    db.add(VisitorGrant(**grant_data(world, scope, account_id=world[owner])))
    db.commit()
    with pytest.raises(api.ManageClaimConflict):
        claim(db, world, monkeypatch)
    db.rollback()
    assert db.query(VisitorClaim).count() == 0


def test_existing_own_grant_can_gain_a_separate_proof_receipt(db, world, monkeypatch):
    existing = VisitorGrant(**grant_data(world))
    db.add(existing)
    db.commit()
    result = claim(db, world, monkeypatch)
    db.commit()
    assert result.grant_id == existing.id and result.created is False
    assert db.query(VisitorGrant).count() == db.query(VisitorClaim).count() == 1


@pytest.mark.parametrize('target', ['client', 'visit', 'business'])
def test_deleted_target_fails_closed(db, world, monkeypatch, target):
    api = core(monkeypatch)
    model = {'client': Client, 'visit': Visit, 'business': Business}[target]
    db.get(model, world[target]).deleted_at = NOW
    db.commit()
    with pytest.raises(api.InvalidManageClaim):
        claim(db, world, monkeypatch)
    db.rollback()
    assert db.query(VisitorGrant).count() == 0


@pytest.mark.parametrize('field', ['business_id', 'client_id'])
def test_spent_receipt_target_change_never_transfers_ownership(db, world, monkeypatch, field):
    api = core(monkeypatch)
    claim(db, world, monkeypatch)
    db.commit()
    if field == 'client_id':
        replacement = Client(full_name='New target')
        db.add(replacement)
        db.flush()
        replacement_id = replacement.id
    else:
        replacement_id = world['other']
    setattr(db.get(Visit, world['visit']), field, replacement_id)
    db.commit()
    with pytest.raises(api.InvalidManageClaim):
        claim(db, world, monkeypatch)
    db.rollback()
    assert db.query(VisitorClaim).one().client_id == world['client']


def test_membership_contact_and_crm_payment_do_not_authorize_or_block_this_scope(db, world, monkeypatch):
    from app.api.core import subscription_access
    monkeypatch.setattr(subscription_access, 'enforce_business_access', lambda *a, **k: pytest.fail('CRM pay gate'))
    db.execute(delete(ClientBusinessRelation))
    db.get(Client, world['client']).contact_email = 'someone.else@example.com'
    db.commit()
    result = claim(db, world, monkeypatch)
    db.commit()
    assert result.client_id == world['client']


def test_rollback_and_actual_deferred_commit_failure_leave_no_half_claim(db, world, monkeypatch):
    claim(db, world, monkeypatch)
    db.rollback()
    assert db.query(VisitorGrant).count() == db.query(VisitorClaim).count() == 0
    claim(db, world, monkeypatch)
    db.execute(text('CREATE TEMP TABLE deferred_claim_failure (value integer UNIQUE DEFERRABLE INITIALLY DEFERRED)'))
    db.execute(text('INSERT INTO deferred_claim_failure VALUES (1),(1)'))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    assert db.query(VisitorGrant).count() == db.query(VisitorClaim).count() == 0
    claim(db, world, monkeypatch)
    db.commit()
    assert db.query(VisitorGrant).count() == db.query(VisitorClaim).count() == 1


def test_concurrent_accounts_one_target_has_one_owner_and_one_receipt(db, world, monkeypatch):
    api = core(monkeypatch)
    engine = db.get_bind()
    def attempt(raw):
        with Session(engine) as worker:
            try:
                result = api.consume_guest_manage(worker, raw, RAW_MANAGE)
                worker.commit()
                return ('success', result.account_id)
            except api.ManageClaimConflict:
                worker.rollback()
                return ('conflict', None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, [world['session_one'], world['session_two']]))
    assert sorted(status for status, _ in outcomes) == ['conflict', 'success']
    receipt, grant = db.query(VisitorClaim).one(), db.query(VisitorGrant).one()
    assert receipt.consumed_account_id == grant.account_id


def test_visit_and_client_anchors_allow_independent_foreign_key_key_share(db, world, monkeypatch):
    claim(db, world, monkeypatch)
    with Session(db.get_bind()) as observer:
        observer.execute(text('SET LOCAL lock_timeout = 300'))
        observer.execute(text('SELECT id FROM visits WHERE id=:id FOR KEY SHARE'), {'id': world['visit']})
        observer.execute(text('SELECT id FROM clients WHERE id=:id FOR KEY SHARE'), {'id': world['client']})
    db.rollback()


@pytest.mark.parametrize('change', ['revoke', 'expire', 'digest', 'business', 'visit', 'clock'])
def test_source_wait_rechecks_fresh_digest_target_revocation_and_time(db, world, monkeypatch, change):
    api = core(monkeypatch)
    engine, reached = db.get_bind(), Event()
    current_time = [NOW]
    monkeypatch.setattr(api, 'utcnow', lambda: current_time[0])
    if change == 'visit':
        other = Visit(client_id=world['client'], business_id=world['business'], specialist_id=world['staff'],
                      date_start=NOW + timedelta(hours=2), date_end=NOW + timedelta(hours=3))
        db.add(other)
        db.commit()
        other_id = other.id
    with Session(engine) as holder:
        source = holder.query(BookingManageToken).filter_by(id=world['token']).with_for_update().one()
        def observe(conn, cursor, statement, parameters, context, executemany):
            if 'FROM booking_manage_tokens' in statement and 'FOR UPDATE' in statement:
                reached.set()
        event.listen(engine, 'before_cursor_execute', observe)
        def attempt():
            with Session(engine) as worker:
                try:
                    api.consume_guest_manage(worker, world['session_one'], RAW_MANAGE)
                    worker.commit()
                    return 'unexpected success'
                except api.InvalidManageClaim:
                    worker.rollback()
                    return 'invalid'
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(attempt)
                if not reached.wait(5):
                    holder.rollback()
                    pytest.fail('Worker did not reach the expected database lock')
                assert not future.done()
                if change == 'revoke':
                    source.revoked_at = NOW
                elif change == 'expire':
                    source.expires_at = NOW
                elif change == 'digest':
                    source.token_hash = 'd' * 64
                elif change == 'business':
                    source.business_id = world['other']
                elif change == 'visit':
                    source.visit_id = other_id
                else:
                    current_time[0] = NOW + timedelta(days=8)
                holder.commit()
                assert future.result(timeout=5) == 'invalid'
        finally:
            event.remove(engine, 'before_cursor_execute', observe)
    assert db.query(VisitorGrant).count() == db.query(VisitorClaim).count() == 0


@pytest.mark.parametrize('change', ['revoke', 'replace_session', 'clock'])
def test_account_wait_rechecks_same_opaque_session_identity(db, world, monkeypatch, change):
    api = core(monkeypatch)
    engine, reached = db.get_bind(), Event()
    current_time = [NOW]
    monkeypatch.setattr(api, 'utcnow', lambda: current_time[0])
    monkeypatch.setattr(visitor_sessions, 'utcnow', lambda: current_time[0])
    with Session(engine) as holder:
        visitor_sessions.lock_verified_account(holder, world['one'])
        def observe(conn, cursor, statement, parameters, context, executemany):
            if 'FROM visitor_accounts' in statement and 'FOR UPDATE' in statement:
                reached.set()
        event.listen(engine, 'before_cursor_execute', observe)
        def attempt():
            with Session(engine) as worker:
                try:
                    api.consume_guest_manage(worker, world['session_one'], RAW_MANAGE)
                    worker.commit()
                    return 'unexpected success'
                except api.InvalidVisitorSession:
                    worker.rollback()
                    return 'invalid'
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(attempt)
                if not reached.wait(5):
                    holder.rollback()
                    pytest.fail('Worker did not reach the expected database lock')
                assert not future.done()
                session = holder.query(VisitorSession).filter_by(
                    token_hash=hashlib.sha256(world['session_one'].encode()).hexdigest()).one()
                if change == 'revoke':
                    session.revoked_at = NOW
                elif change == 'replace_session':
                    original_digest = session.token_hash
                    session.token_hash = 'e' * 64
                    holder.flush()
                    holder.add(VisitorSession(account_id=world['one'], token_hash=original_digest,
                               created_at=NOW, expires_at=NOW + timedelta(days=30)))
                else:
                    current_time[0] = NOW + timedelta(days=31)
                holder.commit()
                assert future.result(timeout=5) == 'invalid'
        finally:
            event.remove(engine, 'before_cursor_execute', observe)
    assert db.query(VisitorGrant).count() == db.query(VisitorClaim).count() == 0


def test_unique_insert_wait_that_rolls_back_rechecks_source_expiry(db, world, monkeypatch):
    api = core(monkeypatch)
    engine, reached = db.get_bind(), Event()
    current_time = [NOW]
    monkeypatch.setattr(api, 'utcnow', lambda: current_time[0])
    with Session(engine) as holder:
        holder.add(VisitorGrant(**grant_data(world, account_id=world['two'])))
        holder.flush()
        def observe(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith('INSERT INTO visitor_grants'):
                reached.set()
        event.listen(engine, 'before_cursor_execute', observe)
        def attempt():
            with Session(engine) as worker:
                try:
                    api.consume_guest_manage(worker, world['session_one'], RAW_MANAGE)
                    worker.commit()
                    return 'unexpected success'
                except api.InvalidManageClaim:
                    worker.rollback()
                    return 'expired'
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(attempt)
                if not reached.wait(5):
                    holder.rollback()
                    pytest.fail('Worker did not reach the unique INSERT wait')
                assert not future.done()
                current_time[0] = NOW + timedelta(days=8)
                holder.rollback()
                assert future.result(timeout=5) == 'expired'
        finally:
            event.remove(engine, 'before_cursor_execute', observe)
    assert db.query(VisitorGrant).count() == db.query(VisitorClaim).count() == 0


def test_client_anchor_wait_observes_new_foreign_client_wide_grant(db, world, monkeypatch):
    api = core(monkeypatch)
    engine, reached = db.get_bind(), Event()
    with Session(engine) as holder:
        holder.query(Client.id).filter_by(id=world['client']).with_for_update(key_share=True).one()
        def observe(conn, cursor, statement, parameters, context, executemany):
            if 'FROM clients' in statement and 'FOR NO KEY UPDATE' in statement:
                reached.set()
        event.listen(engine, 'before_cursor_execute', observe)
        def attempt():
            with Session(engine) as worker:
                try:
                    api.consume_guest_manage(worker, world['session_one'], RAW_MANAGE)
                    worker.commit()
                    return 'unexpected success'
                except api.ManageClaimConflict:
                    worker.rollback()
                    return 'conflict'
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(attempt)
                if not reached.wait(5):
                    holder.rollback()
                    pytest.fail('Worker did not reach the Client anchor wait')
                assert not future.done()
                holder.add(VisitorGrant(**grant_data(world, 'client', account_id=world['two'])))
                holder.commit()
                assert future.result(timeout=5) == 'conflict'
        finally:
            event.remove(engine, 'before_cursor_execute', observe)
    assert db.query(VisitorClaim).count() == 0
    assert db.query(VisitorGrant).one().account_id == world['two']


def test_expiry_between_post_insert_check_and_receipt_timestamp_is_typed(db, world, monkeypatch):
    api = core(monkeypatch)
    db.get(BookingManageToken, world['token']).expires_at = NOW + timedelta(microseconds=1)
    db.commit()
    calls_after_grant = [None]
    def clock():
        if calls_after_grant[0] is None:
            return NOW
        count = calls_after_grant[0]
        calls_after_grant[0] += 1
        return NOW + timedelta(microseconds=count)
    original_flush = db.flush
    def flush(*args, **kwargs):
        new_grant = any(isinstance(row, VisitorGrant) for row in db.new)
        original_flush(*args, **kwargs)
        if new_grant:
            calls_after_grant[0] = 0
    monkeypatch.setattr(api, 'utcnow', clock)
    monkeypatch.setattr(db, 'flush', flush)
    with pytest.raises(api.InvalidManageClaim):
        api.consume_guest_manage(db, world['session_one'], RAW_MANAGE)
    db.rollback()
    assert db.query(VisitorGrant).count() == db.query(VisitorClaim).count() == 0
