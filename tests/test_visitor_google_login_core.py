"""PostgreSQL orchestration after a separately verified Google credential."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
from threading import Barrier, Event
import time

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.core import visitor_challenges, visitor_google_credentials, visitor_google_login, visitor_sessions
from app.api.core.config import settings
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.subscription import Subscription
from app.db.models.user import User
from app.db.models.visitor_account import GOOGLE_ISSUER, VisitorAccount, VisitorGoogleIdentity
from app.db.models.visitor_auth import VisitorChallenge, VisitorSession

NOW = datetime(2026, 10, 26, 12)
ORIGIN = "https://visitor.example"


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    current = [NOW]
    for module in (visitor_challenges, visitor_google_credentials, visitor_google_login, visitor_sessions):
        monkeypatch.setattr(module, "utcnow", lambda: current[0])
    monkeypatch.setattr(settings, "frontend_url", ORIGIN)
    monkeypatch.setattr(settings, "cors_origins", "https://alternate.example")
    return current


def issue(db, *, purpose="google_login", origin=ORIGIN, email=None, now=NOW):
    row = visitor_challenges.issue_challenge(db, purpose, origin=origin, email=email, now=now)
    raw, row_id = row.raw_token, row.row.id
    db.commit()
    return raw, row_id


def claims(raw, *, subject="subject-1", email="visitor@example.com", expiry=NOW + timedelta(hours=1)):
    return visitor_google_credentials.VerifiedGoogleClaims(
        issuer=GOOGLE_ISSUER, subject=subject, email=email, issued_at=NOW,
        expires_at=expiry, nonce_hash=hashlib.sha256((raw or "").encode()).hexdigest())


def existing(db, *, subject="subject-1", email="existing@example.com", password="existing-password-hash"):
    row = VisitorAccount(email_canonical=email, email_verified_at=NOW, hashed_password=password)
    db.add(row)
    db.flush()
    db.add(VisitorGoogleIdentity(issuer=GOOGLE_ISSUER, subject=subject, account_id=row.id))
    row_id = row.id
    db.commit()
    return row_id


def assert_rolled_back(db, proof_id, *, accounts=0, identities=0, sessions=0):
    db.rollback()
    assert db.get(VisitorChallenge, proof_id).consumed_at is None
    assert db.query(VisitorAccount).count() == accounts
    assert db.query(VisitorGoogleIdentity).count() == identities
    assert db.query(VisitorSession).count() == sessions


def run_login(engine, raw, verified, barrier=None):
    with Session(engine) as session:
        try:
            if barrier:
                barrier.wait(timeout=10)
            result = visitor_google_login.login(session, raw, ORIGIN, verified)
            session.commit()
            return "created", result.account_id, result.raw_session_token
        except (visitor_google_login.InvalidGoogleLogin, visitor_google_login.GoogleEmailCollision,
                visitor_google_login.GoogleLoginConflict) as exc:
            session.rollback()
            return type(exc).__name__, None, None


def test_new_identity_is_atomic_and_never_creates_crm_or_outbox_state(db):
    raw, proof_id = issue(db)
    tables = (User, Client, Business, Subscription, NotificationOutbox)
    before = [db.query(table).count() for table in tables]
    result = visitor_google_login.login(db, raw, ORIGIN, claims(raw))
    assert result.raw_session_token.startswith("vst_")
    assert raw not in repr(result) and result.raw_session_token not in repr(result)
    account = db.get(VisitorAccount, result.account_id)
    assert account.email_canonical == "visitor@example.com" and account.hashed_password is None
    assert account.email_verified_at == NOW
    assert db.query(VisitorGoogleIdentity).one().account_id == account.id
    assert db.get(VisitorSession, result.session_id).expires_at - NOW == timedelta(days=30)
    assert db.get(VisitorChallenge, proof_id).consumed_at == NOW
    with Session(db.get_bind()) as observer:
        assert observer.query(VisitorAccount).count() == observer.query(VisitorSession).count() == 0
        assert observer.get(VisitorChallenge, proof_id).consumed_at is None
    assert [db.query(table).count() for table in tables] == before
    for row in (account, db.query(VisitorGoogleIdentity).one(), db.get(VisitorSession, result.session_id),
                db.get(VisitorChallenge, proof_id)):
        values = [getattr(row, column.name) for column in row.__table__.columns]
        assert raw not in values and result.raw_session_token not in values
    db.commit()
    assert visitor_sessions.get_valid_session(db, result.raw_session_token).account_id == account.id


def test_caller_rollback_restores_proof_and_removes_new_identity(db):
    raw, proof_id = issue(db)
    visitor_google_login.login(db, raw, ORIGIN, claims(raw))
    assert_rolled_back(db, proof_id)
    result = visitor_google_login.login(db, raw, ORIGIN, claims(raw))
    db.commit()
    assert visitor_sessions.get_valid_session(db, result.raw_session_token) is not None


def test_existing_subject_never_updates_email_or_password(db):
    account_id = existing(db)
    raw, _ = issue(db)
    result = visitor_google_login.login(db, raw, ORIGIN, claims(raw, email="changed-google@example.com"))
    db.commit()
    assert result.account_id == account_id
    assert db.query(VisitorAccount).count() == db.query(VisitorGoogleIdentity).count() == 1
    assert db.get(VisitorAccount, account_id).email_canonical == "existing@example.com"
    assert db.get(VisitorAccount, account_id).hashed_password == "existing-password-hash"


@pytest.mark.parametrize("password", [None, "existing-scrypt-hash", "$2b$synthetic-legacy"])
def test_email_collision_never_adopts_existing_visitor(db, password):
    account_id = existing(db, subject="another-subject", email="visitor@example.com", password=password)
    raw, proof_id = issue(db)
    with pytest.raises(visitor_google_login.GoogleEmailCollision):
        visitor_google_login.login(db, raw, ORIGIN, claims(raw))
    assert_rolled_back(db, proof_id, accounts=1, identities=1)
    assert db.get(VisitorAccount, account_id).hashed_password == password
    assert db.query(VisitorGoogleIdentity).one().subject == "another-subject"


@pytest.mark.parametrize("origin", [None, "", "*", "https://foreign.example", "https://visitor.example/path",
    "https://user@visitor.example", " https://visitor.example", "https://visitor.example#fragment"])
def test_origin_is_exact_allowlisted_and_does_not_consume_proof(db, origin):
    raw, proof_id = issue(db)
    with pytest.raises(visitor_google_login.InvalidGoogleLogin):
        visitor_google_login.login(db, raw, origin, claims(raw))
    assert_rolled_back(db, proof_id)


def test_allowed_origin_cannot_substitute_the_challenge_origin(db):
    raw, proof_id = issue(db)
    with pytest.raises(visitor_google_login.InvalidGoogleLogin):
        visitor_google_login.login(db, raw, "https://alternate.example", claims(raw))
    assert_rolled_back(db, proof_id)


def test_explicit_alternate_origin_is_allowed_for_its_own_proof(db):
    raw, _ = issue(db, origin="https://alternate.example")
    result = visitor_google_login.login(db, raw, "https://alternate.example", claims(raw))
    db.commit()
    assert visitor_sessions.get_valid_session(db, result.raw_session_token) is not None


@pytest.mark.parametrize("binding", ["0" * 64, "ж" * 64, None, b"bytes", "short"])
def test_verified_nonce_binding_must_match_exact_proof(db, binding):
    raw, proof_id = issue(db)
    with pytest.raises(visitor_google_login.InvalidGoogleLogin):
        visitor_google_login.login(db, raw, ORIGIN, replace(claims(raw), nonce_hash=binding))
    assert_rolled_back(db, proof_id)


@pytest.mark.parametrize("raw", [None, "", "wrong", "vst_" + "A" * 43, "vch_" + "A" * 43])
def test_missing_or_nonvisitor_proof_never_creates_state(db, raw):
    with pytest.raises(visitor_google_login.InvalidGoogleLogin):
        visitor_google_login.login(db, raw, ORIGIN, claims(raw))
    assert db.query(VisitorAccount).count() == db.query(VisitorSession).count() == 0


def test_registration_proof_cannot_be_used_for_google_login(db):
    raw, proof_id = issue(db, purpose="register", email="visitor@example.com", origin=None)
    with pytest.raises(visitor_google_login.InvalidGoogleLogin):
        visitor_google_login.login(db, raw, ORIGIN, claims(raw))
    assert_rolled_back(db, proof_id)


def test_replay_does_not_issue_another_session(db):
    raw, _ = issue(db)
    visitor_google_login.login(db, raw, ORIGIN, claims(raw))
    db.commit()
    with pytest.raises(visitor_google_login.InvalidGoogleLogin):
        visitor_google_login.login(db, raw, ORIGIN, claims(raw))
    db.rollback()
    assert db.query(VisitorAccount).count() == db.query(VisitorSession).count() == 1


@pytest.mark.parametrize("stage", ["before_session", "after_session"])
def test_session_failure_allows_whole_caller_rollback(db, monkeypatch, stage):
    raw, proof_id = issue(db)
    original = visitor_sessions.create_session
    def fail(*args, **kwargs):
        if stage == "after_session":
            original(*args, **kwargs)
        raise RuntimeError("synthetic session failure")
    monkeypatch.setattr(visitor_sessions, "create_session", fail)
    with pytest.raises(RuntimeError, match="synthetic session failure"):
        visitor_google_login.login(db, raw, ORIGIN, claims(raw))
    assert_rolled_back(db, proof_id)


def test_real_deferred_commit_failure_restores_whole_transaction(db):
    raw, proof_id = issue(db)
    db.execute(text("""CREATE FUNCTION google_review_commit_failure() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'synthetic deferred failure'; RETURN NEW; END $$"""))
    db.execute(text("""CREATE CONSTRAINT TRIGGER google_review_commit_failure AFTER INSERT ON visitor_sessions
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION google_review_commit_failure()"""))
    db.commit()
    try:
        visitor_google_login.login(db, raw, ORIGIN, claims(raw))
        with pytest.raises(DBAPIError):
            db.commit()
        assert_rolled_back(db, proof_id)
    finally:
        db.rollback()
        db.execute(text("DROP TRIGGER google_review_commit_failure ON visitor_sessions"))
        db.execute(text("DROP FUNCTION google_review_commit_failure()"))
        db.commit()


def test_same_proof_race_issues_exactly_one_session(db):
    raw, proof_id = issue(db)
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run_login(db.get_bind(), raw, claims(raw), barrier), range(2)))
    assert sorted(row[0] for row in results) == ["InvalidGoogleLogin", "created"]
    db.expire_all()
    assert db.query(VisitorAccount).count() == db.query(VisitorGoogleIdentity).count() == db.query(VisitorSession).count() == 1
    assert db.get(VisitorChallenge, proof_id).consumed_at is not None


@pytest.mark.parametrize("same_subject,same_email", [(True, True), (True, False), (False, True)])
def test_unique_races_leave_no_orphan_and_losing_proof_is_reusable(db, same_subject, same_email):
    one, one_id = issue(db)
    two, two_id = issue(db)
    verified = [claims(one), claims(two, subject="subject-1" if same_subject else "subject-2",
        email="visitor@example.com" if same_email else "other@example.com")]
    barrier = Barrier(2)
    def synchronize(conn, cursor, statement, parameters, context, executemany):
        marker = "INSERT INTO VISITOR_ACCOUNTS" if same_email else "INSERT INTO VISITOR_GOOGLE_IDENTITIES"
        if statement.lstrip().upper().startswith(marker):
            barrier.wait(timeout=10)
    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", synchronize)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run_login, engine, raw, value) for raw, value in zip([one, two], verified)]
            results = [future.result(timeout=15) for future in futures]
    finally:
        event.remove(engine, "before_cursor_execute", synchronize)
    assert sorted(row[0] for row in results) == ["GoogleLoginConflict", "created"]
    db.expire_all()
    assert db.query(VisitorAccount).count() == db.query(VisitorGoogleIdentity).count() == db.query(VisitorSession).count() == 1
    assert sum(db.get(VisitorChallenge, row_id).consumed_at is not None for row_id in [one_id, two_id]) == 1
    loser = next(i for i, row in enumerate(results) if row[0] != "created")
    if same_subject:
        result = visitor_google_login.login(db, [one, two][loser], ORIGIN, verified[loser])
        db.commit()
        assert result.account_id == next(row[1] for row in results if row[0] == "created")
        assert db.query(VisitorAccount).count() == 1 and db.query(VisitorSession).count() == 2
    else:
        with pytest.raises(visitor_google_login.GoogleEmailCollision):
            visitor_google_login.login(db, [one, two][loser], ORIGIN, verified[loser])
        db.rollback()
        assert db.query(VisitorSession).count() == 1


@pytest.mark.parametrize("change", ["delete", "mapping", "unlink", "reset", "jwt_expire", "proof_expire"])
def test_existing_identity_rechecks_after_account_lock_wait(db, monkeypatch, clock, change):
    account_id = existing(db)
    other = VisitorAccount(email_canonical="other@example.com", email_verified_at=NOW)
    db.add(other)
    old, old_raw = visitor_sessions.create_session(db, db.get(VisitorAccount, account_id))
    old_id = old.id
    db.commit()
    raw, proof_id = issue(db)
    verified = claims(raw, expiry=NOW + timedelta(minutes=1) if change == "jwt_expire" else NOW + timedelta(hours=1))
    entered = Event()
    original = visitor_sessions.lock_verified_account
    def observe(session, owner):
        entered.set()
        return original(session, owner)
    engine = db.get_bind()
    with Session(engine) as holder:
        account = original(holder, account_id)
        monkeypatch.setattr(visitor_sessions, "lock_verified_account", observe)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(run_login, engine, raw, verified)
            try:
                assert entered.wait(timeout=5)
                if change == "delete":
                    holder.delete(account)
                elif change == "mapping":
                    holder.query(VisitorGoogleIdentity).filter_by(subject="subject-1").update({"account_id": other.id})
                elif change == "unlink":
                    holder.query(VisitorGoogleIdentity).filter_by(subject="subject-1").delete()
                elif change == "reset":
                    account.hashed_password = "reset-password-hash"
                    visitor_sessions.revoke_all_sessions(holder, account_id=account_id)
                elif change == "jwt_expire":
                    clock[0] = verified.expires_at
                else:
                    clock[0] = NOW + timedelta(minutes=5)
                holder.commit()
                outcome = future.result(timeout=15)
            finally:
                holder.rollback()
    db.expire_all()
    if change == "reset":
        assert outcome[0] == "created"
        assert db.get(VisitorSession, old_id).revoked_at is not None
        assert visitor_sessions.get_valid_session(db, old_raw) is None
        assert visitor_sessions.get_valid_session(db, outcome[2]) is not None
        assert db.get(VisitorAccount, account_id).hashed_password == "reset-password-hash"
    else:
        assert outcome[0] in {"InvalidGoogleLogin", "GoogleLoginConflict"}
        assert db.get(VisitorChallenge, proof_id).consumed_at is None
        assert db.query(VisitorSession).count() == (0 if change == "delete" else 1)


@pytest.mark.parametrize("expire,offset", [("jwt", -1), ("jwt", 0), ("jwt", 1),
                                        ("proof", -1), ("proof", 0), ("proof", 1)])
def test_new_account_unique_wait_rechecks_jwt_and_proof_expiry(db, clock, expire, offset):
    raw, proof_id = issue(db)
    expiry = NOW + timedelta(minutes=1 if expire == "jwt" else 5)
    verified = claims(raw, expiry=expiry if expire == "jwt" else NOW + timedelta(hours=1))
    engine = db.get_bind()
    entered = Event()
    observed = {}
    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT INTO VISITOR_ACCOUNTS"):
            observed["pid"] = conn.connection.driver_connection.get_backend_pid()
            entered.set()
    with Session(engine) as holder:
        holder.add(VisitorAccount(email_canonical="visitor@example.com", email_verified_at=NOW))
        holder.flush()
        holder_pid = holder.execute(text("SELECT pg_backend_pid()")).scalar_one()
        event.listen(engine, "before_cursor_execute", capture)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(run_login, engine, raw, verified)
                try:
                    assert entered.wait(timeout=5)
                    deadline = time.monotonic() + 5
                    with engine.connect() as observer:
                        while time.monotonic() < deadline:
                            blocked_by = observer.execute(text("SELECT pg_blocking_pids(:pid)"), {"pid": observed["pid"]}).scalar_one()
                            if holder_pid in blocked_by:
                                break
                            time.sleep(0.01)
                        else:
                            pytest.fail("PostgreSQL did not confirm the actual unique-index wait")
                    clock[0] = expiry + timedelta(seconds=offset)
                finally:
                    holder.rollback()
                outcome = future.result(timeout=15)
        finally:
            event.remove(engine, "before_cursor_execute", capture)
    db.expire_all()
    if offset < 0:
        assert outcome[0] == "created"
        assert db.query(VisitorAccount).count() == db.query(VisitorSession).count() == 1
    else:
        assert outcome[0] == "InvalidGoogleLogin"
        assert db.query(VisitorAccount).count() == db.query(VisitorGoogleIdentity).count() == db.query(VisitorSession).count() == 0
        assert db.get(VisitorChallenge, proof_id).consumed_at is None
