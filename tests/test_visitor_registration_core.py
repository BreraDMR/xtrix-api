"""Registration completion uses real proof locks and canonical-email uniqueness."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier, Event
import time

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.core import visitor_challenges, visitor_passwords, visitor_registration, visitor_sessions
from app.api.core.clock import utcnow
from app.api.core.visitor_registration import (
    InvalidRegistrationPassword, InvalidRegistrationProof, RegistrationConflict, complete_registration,
)
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.subscription import Subscription
from app.db.models.user import User
from app.db.models.visitor_account import VisitorAccount, VisitorGoogleIdentity
from app.db.models.visitor_auth import VisitorChallenge, VisitorSession

PASSWORD = "Safe!Visitor-57-Pass"


def proof(db, email="guest@example.com", **kwargs):
    issued = visitor_challenges.issue_challenge(db, "register", email=email, **kwargs)
    result = issued.raw_token, issued.row.id
    db.commit()
    return result


def assert_clean_after_rollback(db, proof_id):
    db.rollback()
    assert db.get(VisitorChallenge, proof_id).consumed_at is None
    assert db.query(VisitorAccount).count() == 0
    assert db.query(VisitorSession).count() == 0


def test_completion_flushes_all_without_committing_or_creating_crm_state(db):
    token, proof_id = proof(db, "  Guest.Name+tag@EXAMPLE.COM  ")
    tables = (User, Client, Business, Subscription, NotificationOutbox, VisitorGoogleIdentity)
    before = [db.query(table).count() for table in tables]
    assert db.query(VisitorAccount).count() == 0
    result = complete_registration(db, token, PASSWORD)
    assert result.account.email_canonical == "guest.name+tag@example.com"
    assert result.account.email_verified_at is not None
    assert visitor_passwords.verify_password(PASSWORD, result.account.hashed_password)
    assert result.session.account_id == result.account.id
    assert result.raw_session_token not in repr(result)
    assert result.raw_session_token.startswith("vst_")
    assert result.session.expires_at - result.session.created_at == timedelta(days=30)
    assert db.get(VisitorChallenge, proof_id).consumed_at is not None
    persisted = [result.account, result.session, db.get(VisitorChallenge, proof_id)]
    stored_values = [str(getattr(row, column.name)) for row in persisted for column in row.__table__.columns]
    assert all(secret not in stored_values for secret in (token, PASSWORD, result.raw_session_token))
    with Session(db.get_bind()) as observer:
        assert observer.query(VisitorAccount).count() == 0
        assert observer.query(VisitorSession).count() == 0
        assert observer.get(VisitorChallenge, proof_id).consumed_at is None
    assert [db.query(table).count() for table in tables] == before
    db.commit()
    assert visitor_sessions.get_valid_session(db, result.raw_session_token).account_id == result.account.id


@pytest.mark.parametrize("token", [None, "", "invalid", "vst_" + "A" * 43])
def test_invalid_proof_never_reaches_password_hashing_or_creates_state(db, monkeypatch, token):
    monkeypatch.setattr(visitor_passwords, "hash_password", lambda *_: pytest.fail("invalid proof reached KDF"))
    with pytest.raises(InvalidRegistrationProof):
        complete_registration(db, token, PASSWORD)
    assert db.query(VisitorAccount).count() == db.query(VisitorSession).count() == 0


def test_wrong_purpose_is_never_used_as_registration_proof(db, monkeypatch):
    issued = visitor_challenges.issue_challenge(db, "google_login", origin="https://visitor.example")
    token, row_id = issued.raw_token, issued.row.id
    db.commit()
    monkeypatch.setattr(visitor_passwords, "hash_password", lambda *_: pytest.fail("wrong purpose reached KDF"))
    with pytest.raises(InvalidRegistrationProof):
        complete_registration(db, token, PASSWORD)
    assert db.get(VisitorChallenge, row_id).consumed_at is None
    assert db.query(VisitorAccount).count() == 0


@pytest.mark.parametrize("password", ["short", "lowercase-only-57", "A!" * 65, True, "Safe!Text57\ud800"])
def test_creation_policy_failure_is_generic_and_caller_rollback_restores_proof(db, password):
    token, proof_id = proof(db)
    with pytest.raises(InvalidRegistrationPassword) as failure:
        complete_registration(db, token, password)
    assert str(failure.value) == "Password does not meet the creation policy"
    assert_clean_after_rollback(db, proof_id)


def test_policy_is_scoped_to_creation_and_full_unicode_secret_is_not_trimmed_or_truncated(db, monkeypatch):
    from app.api.core import security
    monkeypatch.setattr(security, "get_password_hash", lambda *_: pytest.fail("CRM bcrypt was used"))
    token, _ = proof(db)
    password = " " + "A!b7Жю" * 20 + " "
    assert len(password) <= 128 and len(password.encode()) > 72
    result = complete_registration(db, token, password)
    db.commit()
    assert visitor_passwords.verify_password(password, result.account.hashed_password)
    assert not visitor_passwords.verify_password(password.strip(), result.account.hashed_password)
    assert not visitor_passwords.verify_password(password[:-2] + "Q ", result.account.hashed_password)


@pytest.mark.parametrize("original_hash", [None, "$2b$synthetic-unsupported-crm-hash", "corrupt-existing-hash", "valid-visitor-scrypt"])
def test_existing_account_collision_never_overwrites_password_google_identity_or_sessions(db, original_hash):
    if original_hash == "valid-visitor-scrypt":
        original_hash = visitor_passwords.hash_password("Prior!Visitor-68-Key")
    existing = VisitorAccount(email_canonical="guest@example.com", email_verified_at=utcnow(), hashed_password=original_hash)
    db.add(existing)
    db.flush()
    db.add(VisitorGoogleIdentity(subject="existing-google-subject", account_id=existing.id))
    original_session, original_raw = visitor_sessions.create_session(db, existing)
    existing_id, session_id = existing.id, original_session.id
    db.commit()
    token, proof_id = proof(db)
    with pytest.raises(RegistrationConflict):
        complete_registration(db, token, PASSWORD)
    db.rollback()
    assert db.get(VisitorChallenge, proof_id).consumed_at is None
    assert db.query(VisitorAccount).count() == 1
    assert db.get(VisitorAccount, existing_id).hashed_password == original_hash
    assert db.query(VisitorGoogleIdentity).one().account_id == existing_id
    assert visitor_sessions.get_valid_session(db, original_raw).id == session_id
    assert db.query(VisitorSession).count() == 1


def test_caller_rollback_restores_consumed_proof_after_successful_flush(db):
    token, proof_id = proof(db)
    result = complete_registration(db, token, PASSWORD)
    assert result.account.id and result.session.id
    assert_clean_after_rollback(db, proof_id)
    second = complete_registration(db, token, PASSWORD)
    db.commit()
    assert visitor_sessions.get_valid_session(db, second.raw_session_token) is not None


@pytest.mark.parametrize("stage", ["kdf", "before_session", "after_session"])
def test_failure_in_each_stage_requires_and_allows_whole_caller_rollback(db, monkeypatch, stage):
    token, proof_id = proof(db)
    def fail(*_args, **_kwargs):
        raise RuntimeError("synthetic failure")
    if stage == "kdf":
        monkeypatch.setattr(visitor_passwords, "hash_password", fail)
    elif stage == "before_session":
        monkeypatch.setattr(visitor_sessions, "create_session", fail)
    else:
        original = visitor_sessions.create_session
        def fail_after(*args, **kwargs):
            original(*args, **kwargs)
            fail()
        monkeypatch.setattr(visitor_sessions, "create_session", fail_after)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        complete_registration(db, token, PASSWORD)
    assert_clean_after_rollback(db, proof_id)


def test_database_commit_failure_rolls_back_proof_account_and_session(db):
    token, proof_id = proof(db)
    db.execute(text("""CREATE FUNCTION review_registration_commit_failure() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'synthetic deferred commit failure'; RETURN NEW; END $$"""))
    db.execute(text("""CREATE CONSTRAINT TRIGGER review_registration_commit_failure
        AFTER INSERT ON visitor_sessions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION review_registration_commit_failure()"""))
    db.commit()
    try:
        result = complete_registration(db, token, PASSWORD)
        assert result.account.id and result.session.id
        with pytest.raises(DBAPIError):
            db.commit()
        assert_clean_after_rollback(db, proof_id)
    finally:
        db.rollback()
        db.execute(text("DROP TRIGGER review_registration_commit_failure ON visitor_sessions"))
        db.execute(text("DROP FUNCTION review_registration_commit_failure()"))
        db.commit()


def _complete_in_session(engine, token, barrier=None):
    with Session(engine) as session:
        try:
            if barrier:
                barrier.wait(timeout=10)
            result = complete_registration(session, token, PASSWORD)
            account_id = result.account.id
            session.commit()
            return "created", account_id, result.raw_session_token
        except (InvalidRegistrationProof, RegistrationConflict) as exc:
            session.rollback()
            return type(exc).__name__, None, None


def test_same_proof_race_creates_exactly_one_account_and_one_session(db):
    token, proof_id = proof(db)
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(lambda _: _complete_in_session(db.get_bind(), token, barrier), range(2)))
    assert sorted(row[0] for row in rows) == ["InvalidRegistrationProof", "created"]
    db.expire_all()
    assert db.query(VisitorAccount).count() == db.query(VisitorSession).count() == 1
    assert db.get(VisitorChallenge, proof_id).consumed_at is not None
    assert sum(row[2] is not None for row in rows) == 1


def test_different_proofs_same_email_race_uses_unique_index_and_restores_losing_proof(db, monkeypatch):
    first, first_id = proof(db, " GUEST@EXAMPLE.COM ")
    second, second_id = proof(db, "guest@example.com")
    barrier = Barrier(2)
    original = visitor_passwords.hash_password
    def both_hash(password):
        encoded = original(password)
        barrier.wait(timeout=10)
        return encoded
    monkeypatch.setattr(visitor_passwords, "hash_password", both_hash)
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(lambda token: _complete_in_session(db.get_bind(), token), [first, second]))
    assert sorted(row[0] for row in rows) == ["RegistrationConflict", "created"]
    db.expire_all()
    assert db.query(VisitorAccount).count() == db.query(VisitorSession).count() == 1
    assert sum(db.get(VisitorChallenge, row_id).consumed_at is not None for row_id in (first_id, second_id)) == 1
    assert sum(row[2] is not None for row in rows) == 1


def test_existing_account_row_lock_is_not_taken_by_registration_collision(db):
    existing = VisitorAccount(email_canonical="guest@example.com", email_verified_at=utcnow())
    db.add(existing)
    db.commit()
    existing_id = existing.id
    token, proof_id = proof(db)
    with Session(db.get_bind()) as holder:
        holder.query(VisitorAccount).filter_by(id=existing_id).with_for_update().one()
        with Session(db.get_bind()) as waiter:
            waiter.execute(text("SET LOCAL lock_timeout='500ms'"))
            with pytest.raises(RegistrationConflict):
                complete_registration(waiter, token, PASSWORD)
            waiter.rollback()
        holder.rollback()
    db.expire_all()
    assert db.get(VisitorChallenge, proof_id).consumed_at is None
    assert db.get(VisitorAccount, existing_id).hashed_password is None
    assert db.query(VisitorSession).count() == 0


def test_only_our_new_account_is_locked_after_the_register_challenge(db):
    unrelated = VisitorAccount(email_canonical="other@example.com", email_verified_at=utcnow())
    db.add(unrelated)
    db.commit()
    token, _ = proof(db)
    locked = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        if "visitor_accounts" in statement.lower() and "for update" in statement.lower():
            locked.append(parameters)
    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        result = complete_registration(db, token, PASSWORD)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(locked) == 1
    assert result.account.id in locked[0].values()
    assert unrelated.id not in locked[0].values()
    db.rollback()


def test_expiry_is_rechecked_after_waiting_for_challenge_lock_before_hashing(db, monkeypatch):
    now = datetime(2026, 10, 26, 12)
    token, proof_id = proof(db, now=now)
    clock = [now]
    monkeypatch.setattr(visitor_challenges, "utcnow", lambda: clock[0])
    monkeypatch.setattr(visitor_passwords, "hash_password", lambda *_: pytest.fail("expired proof reached KDF"))
    entering_lock = Event()
    engine = db.get_bind()
    def capture(conn, cursor, statement, parameters, context, executemany):
        if "visitor_challenges" in statement.lower() and "for update" in statement.lower():
            entering_lock.set()
    with Session(engine) as holder:
        holder.query(VisitorChallenge).filter_by(id=proof_id).with_for_update().one()
        event.listen(engine, "before_cursor_execute", capture)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(_complete_in_session, engine, token)
                assert entering_lock.wait(timeout=5)
                clock[0] = now + timedelta(minutes=30)
                holder.commit()
                result = future.result(timeout=10)
        finally:
            event.remove(engine, "before_cursor_execute", capture)
    assert result[0] == "InvalidRegistrationProof"
    db.expire_all()
    assert db.get(VisitorChallenge, proof_id).consumed_at is None
    assert db.query(VisitorAccount).count() == db.query(VisitorSession).count() == 0


@pytest.mark.parametrize("expiry_offset_seconds", [-1, 0, 1])
def test_registration_rechecks_expiry_after_real_unique_insert_wait(db, monkeypatch, expiry_offset_seconds):
    start = datetime(2026, 10, 26, 12)
    issued = visitor_challenges.issue_challenge(db, "register", email="insert-wait@example.com", now=start)
    raw, proof_id, expiry = issued.raw_token, issued.row.id, issued.row.expires_at
    db.commit()
    clock = [start]
    for module in (visitor_challenges, visitor_registration, visitor_sessions):
        monkeypatch.setattr(module, "utcnow", lambda: clock[0])
    engine = db.get_bind()
    inserting = Event()
    observed = {}

    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT INTO VISITOR_ACCOUNTS"):
            observed["waiter_pid"] = conn.connection.driver_connection.get_backend_pid()
            inserting.set()

    def complete():
        with Session(engine) as waiter:
            try:
                result = visitor_registration.complete_registration(waiter, raw, PASSWORD)
                created_at = result.session.created_at
                waiter.commit()
                return "created", created_at
            except InvalidRegistrationProof:
                waiter.rollback()
                return "invalid-proof", None

    with Session(engine) as holder:
        holder.add(VisitorAccount(email_canonical="insert-wait@example.com", email_verified_at=start))
        holder.flush()
        holder_pid = holder.execute(text("SELECT pg_backend_pid()")).scalar_one()
        event.listen(engine, "before_cursor_execute", capture)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(complete)
                try:
                    assert inserting.wait(timeout=10), "waiter did not reach account INSERT"
                    deadline = time.monotonic() + 5
                    blocked = False
                    with engine.connect() as observer:
                        while time.monotonic() < deadline:
                            blockers = observer.execute(text("SELECT pg_blocking_pids(:pid)"), {"pid": observed["waiter_pid"]}).scalar_one()
                            if holder_pid in blockers:
                                blocked = True
                                break
                            time.sleep(0.01)
                    assert blocked, "PostgreSQL did not confirm the actual unique-index wait"
                    assert not future.done(), "the insert did not remain blocked"
                    clock[0] = expiry + timedelta(seconds=expiry_offset_seconds)
                finally:
                    holder.rollback()
                outcome, created_at = future.result(timeout=15)
        finally:
            event.remove(engine, "before_cursor_execute", capture)
    db.expire_all()
    if expiry_offset_seconds < 0:
        assert outcome == "created"
        assert created_at < expiry
        assert db.query(VisitorAccount).count() == db.query(VisitorSession).count() == 1
        assert db.get(VisitorChallenge, proof_id).consumed_at is not None
    else:
        assert outcome == "invalid-proof", f"registration completed after proof expiry; session delta={created_at - expiry}"
        assert db.get(VisitorChallenge, proof_id).consumed_at is None
        assert db.query(VisitorAccount).count() == db.query(VisitorSession).count() == 0
