"""Independent collision and cross-module account-lock probes."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.core import visitor_email_change as core
from app.db.models.visitor_account import VisitorAccount
from app.db.models.visitor_auth import VisitorChallenge, VisitorSession
from tests import test_visitor_email_change as original


@pytest.fixture
def world(db, monkeypatch):
    return original.world.__wrapped__(db, monkeypatch)


def test_existing_address_swap_with_noop_row_version_is_typed_and_atomic(db, world):
    engine = db.get_bind()
    old_addresses = {row.id: row.email_canonical for row in db.query(VisitorAccount)}
    one = original.proof(db, world, old_addresses[world['two']], session=world['sessions'][0])
    two = original.proof(db, world, old_addresses[world['one']], session=world['sessions'][2])
    def run(worker, raw_session, raw_proof):
        try:
            core.confirm(worker, raw_session, raw_proof)
            worker.commit()
            return 'unexpected success'
        except core.EmailChangeConflict:
            worker.rollback()
            return 'EmailChangeConflict'
        except DBAPIError as error:
            code = error.orig.pgcode
            worker.rollback()
            return code
    def first_actor():
        with Session(engine, autoflush=False) as worker:
            worker.execute(text("SET LOCAL deadlock_timeout='50ms'"))
            worker.execute(text("SET LOCAL lock_timeout='5s'"))
            return run(worker, world['sessions'][0], one.raw_token)
    with Session(engine, autoflush=False) as holder:
        holder.execute(text("SET LOCAL deadlock_timeout='50ms'"))
        holder.execute(text("SET LOCAL lock_timeout='5s'"))
        from app.api.core import visitor_sessions
        visitor_sessions.lock_verified_account(holder, world['two'])
        before = holder.execute(text('SELECT email_canonical,email_verified_at FROM visitor_accounts WHERE id=:id'),
                                {'id': world['two']}).one()
        # Only create a pending row version; both persisted contact values remain identical.
        holder.execute(text('UPDATE visitor_accounts SET email_verified_at=email_verified_at WHERE id=:id'),
                       {'id': world['two']})
        assert holder.execute(text('SELECT email_canonical,email_verified_at FROM visitor_accounts WHERE id=:id'),
                              {'id': world['two']}).one() == before
        holder_pid = holder.execute(text('SELECT pg_backend_pid()')).scalar_one()
        with original.real_wait(engine, lambda sql: sql.startswith('UPDATE visitor_accounts') and 'email_canonical' in sql) as blocked:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(first_actor)
                try:
                    blocked(holder_pid)
                    second = run(holder, world['sessions'][2], two.raw_token)
                    first = future.result(timeout=8)
                finally:
                    holder.rollback()
    db.expire_all()
    assert {row.id: row.email_canonical for row in db.query(VisitorAccount)} == old_addresses
    assert db.get(VisitorChallenge, one.challenge_id).consumed_at is None
    assert db.get(VisitorChallenge, two.challenge_id).consumed_at is None
    assert all(row.revoked_at is None for row in db.query(VisitorSession))
    assert sorted([first, second]) == ['EmailChangeConflict', 'EmailChangeConflict']


def test_password_reset_winning_account_lock_invalidates_waiting_email_actor(db, world, monkeypatch):
    from app.api.core import visitor_challenges, visitor_password_reset
    from app.db.models.visitor_account import VisitorGoogleIdentity
    from app.db.models.visitor_access import VisitorGrant
    monkeypatch.setattr(visitor_password_reset, 'utcnow', lambda: world['clock'][0])
    prepared = original.proof(db, world)
    reset = visitor_challenges.issue_challenge(db, 'reset', account_id=world['one'], email=world['old_email'])
    raw_reset, reset_id = reset.raw_token, reset.row.id
    db.commit()
    engine = db.get_bind()
    principals = {row.subject: row.account_id for row in db.query(VisitorGoogleIdentity)}
    with Session(engine) as holder:
        visitor_password_reset.confirm_reset(holder, raw_reset, 'Different!Password-948')
        holder_pid = holder.execute(text('SELECT pg_backend_pid()')).scalar_one()
        with original.real_wait(engine, lambda sql: 'FROM visitor_accounts' in sql and 'FOR UPDATE' in sql) as blocked:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(original.confirm_attempt, engine, world['sessions'][0], prepared.raw_token)
                try:
                    blocked(holder_pid)
                    holder.commit()
                    assert future.result(timeout=8) == ('VisitorReauthenticationRequired', None)
                finally:
                    holder.rollback()
    db.expire_all()
    assert db.get(VisitorAccount, world['one']).email_canonical == world['old_email']
    assert db.get(VisitorAccount, world['one']).hashed_password != 'preserved-visitor-password-canary'
    assert db.get(VisitorChallenge, prepared.challenge_id).consumed_at is None
    assert db.get(VisitorChallenge, reset_id).consumed_at is not None
    assert all(db.get(VisitorSession, ident).revoked_at is not None for ident in world['session_ids'][:2])
    assert db.get(VisitorSession, world['session_ids'][2]).revoked_at is None
    assert {row.subject: row.account_id for row in db.query(VisitorGoogleIdentity)} == principals
    assert db.query(VisitorGrant).one().account_id == world['one']
