"""Reset proof, password and session revocation share one real DB transaction."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta
from threading import Barrier, Event
import time

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.core import visitor_challenges, visitor_password_reset, visitor_passwords, visitor_sessions
from app.api.core.visitor_password_reset import InvalidResetPassword, InvalidResetProof, confirm_reset
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.subscription import Subscription
from app.db.models.user import User
from app.db.models.visitor_account import VisitorAccount, VisitorGoogleIdentity
from app.db.models.visitor_auth import VisitorChallenge, VisitorSession

NOW = datetime(2026, 10, 26, 12)
OLD = "Prior!Visitor-68-Key"
NEW = "New!Visitor-59-Pass"


@pytest.fixture
def world(db, monkeypatch):
    clock = [NOW]
    for module in (visitor_sessions, visitor_challenges, visitor_password_reset):
        monkeypatch.setattr(module, "utcnow", lambda: clock[0])
    owner = VisitorAccount(email_canonical="reset.owner@example.com", email_verified_at=NOW,
                           hashed_password=visitor_passwords.hash_password(OLD))
    other = VisitorAccount(email_canonical="other@example.com", email_verified_at=NOW)
    crm = User(full_name="Synthetic CRM owner", email=owner.email_canonical,
               hashed_password="synthetic-CRM-password-canary", role="specialist")
    db.add_all([owner, other, crm])
    db.flush()
    db.add(VisitorGoogleIdentity(subject="preserved-google-subject", account_id=owner.id))
    rows, raws = [], []
    for account in (owner, owner, other):
        row, raw = visitor_sessions.create_session(db, account)
        rows.append(row.id)
        raws.append(raw)
    result = {"clock": clock, "account_id": owner.id, "other_id": other.id,
              "old_hash": owner.hashed_password, "sessions": rows, "raws": raws, "crm_id": crm.id}
    db.commit()
    return result


def issue(db, world, *, account_id=None):
    account = db.get(VisitorAccount, account_id or world["account_id"])
    result = visitor_challenges.issue_challenge(db, "reset", account_id=account.id,
                                                email=account.email_canonical)
    raw, row_id = result.raw_token, result.row.id
    db.commit()
    return raw, row_id


def assert_rolled_back(db, world, proof_ids):
    db.rollback()
    assert db.get(VisitorAccount, world["account_id"]).hashed_password == world["old_hash"]
    assert all(db.get(VisitorChallenge, row_id).consumed_at is None for row_id in proof_ids)
    assert all(db.get(VisitorSession, row_id).revoked_at is None for row_id in world["sessions"])
    assert db.query(VisitorSession).count() == 3


def test_reset_preserves_pending_password_revokes_all_old_sessions_and_changes_no_other_identity(db, world):
    raw, proof_id = issue(db, world)
    tables = (User, Client, Business, Subscription, NotificationOutbox, VisitorAccount, VisitorGoogleIdentity)
    before = [db.query(table).count() for table in tables]
    result = confirm_reset(db, raw, NEW)
    assert result.account_id == world["account_id"] and result.revoked_sessions == 2
    changed = db.get(VisitorAccount, result.account_id)
    assert visitor_passwords.verify_password(NEW, changed.hashed_password)
    assert not visitor_passwords.verify_password(OLD, changed.hashed_password)
    with Session(db.get_bind()) as observer:
        assert observer.get(VisitorAccount, result.account_id).hashed_password == world["old_hash"]
        assert observer.get(VisitorChallenge, proof_id).consumed_at is None
        assert observer.get(VisitorSession, world["sessions"][0]).revoked_at is None
    db.commit()
    assert all(visitor_sessions.get_valid_session(db, raw) is None for raw in world["raws"][:2])
    assert visitor_sessions.get_valid_session(db, world["raws"][2]) is not None
    assert db.query(VisitorSession).count() == 3
    assert db.get(User, world["crm_id"]).hashed_password == "synthetic-CRM-password-canary"
    assert db.query(VisitorGoogleIdentity).one().subject == "preserved-google-subject"
    assert changed.email_canonical == "reset.owner@example.com"
    assert [db.query(table).count() for table in tables] == before
    persisted = [changed, db.get(VisitorChallenge, proof_id), *db.query(VisitorSession).all()]
    assert all(secret not in [str(getattr(row, column.name)) for row in persisted for column in row.__table__.columns]
               for secret in [raw, NEW])
    assert raw not in repr(result) and NEW not in repr(result)


def test_explicit_email_reset_can_set_google_only_password_without_changing_google(db, world):
    account = db.get(VisitorAccount, world["account_id"])
    account.hashed_password = None
    db.commit()
    raw, _ = issue(db, world)
    confirm_reset(db, raw, NEW)
    db.commit()
    assert visitor_passwords.verify_password(NEW, account.hashed_password)
    assert db.query(VisitorGoogleIdentity).one().account_id == account.id
    assert db.query(VisitorSession).count() == 3


def test_success_invalidates_sibling_resets_only_and_rollback_restores_them(db, world):
    first, first_id = issue(db, world)
    sibling, sibling_id = issue(db, world)
    _, foreign_id = issue(db, world, account_id=world["other_id"])
    register = visitor_challenges.issue_challenge(db, "register", email="reset.owner@example.com")
    google_link = visitor_challenges.issue_challenge(db, "google_link", account_id=world["account_id"], origin="https://visitor.example")
    controls = [foreign_id, register.row.id, google_link.row.id]
    db.commit()
    confirm_reset(db, first, NEW)
    assert db.get(VisitorChallenge, sibling_id).consumed_at == NOW
    assert_rolled_back(db, world, [first_id, sibling_id, *controls])
    confirm_reset(db, first, NEW)
    db.commit()
    assert db.get(VisitorChallenge, first_id).consumed_at == db.get(VisitorChallenge, sibling_id).consumed_at == NOW
    assert all(db.get(VisitorChallenge, row_id).consumed_at is None for row_id in controls)
    for raw in (first, sibling):
        with pytest.raises(InvalidResetProof):
            confirm_reset(db, raw, "Next!Visitor-47-Pass")
        db.rollback()


@pytest.mark.parametrize("raw", [None, "", "wrong", "vst_" + "A" * 43])
def test_invalid_proof_never_hashes_or_changes_state(db, world, monkeypatch, raw):
    monkeypatch.setattr(visitor_passwords, "hash_password", lambda *_: pytest.fail("Invalid proof reached KDF"))
    with pytest.raises(InvalidResetProof):
        confirm_reset(db, raw, NEW)
    assert db.get(VisitorAccount, world["account_id"]).hashed_password == world["old_hash"]
    assert all(row.revoked_at is None for row in db.query(VisitorSession).all())


@pytest.mark.parametrize("purpose", ["register", "google_login", "google_link", "email_change"])
def test_other_proof_purposes_cannot_reset_password(db, world, purpose):
    args = {"email": "reset.owner@example.com"} if purpose in {"register", "email_change"} else {"origin": "https://visitor.example"}
    if purpose in {"email_change", "google_link"}:
        args["account_id"] = world["account_id"]
    proof = visitor_challenges.issue_challenge(db, purpose, **args)
    raw, proof_id = proof.raw_token, proof.row.id
    db.commit()
    with pytest.raises(InvalidResetProof):
        confirm_reset(db, raw, NEW)
    assert_rolled_back(db, world, [proof_id])


@pytest.mark.parametrize("password", ["short", "lowercase-only-57", True, "A!" * 65, "Safe!Text57\ud800"])
def test_invalid_new_password_is_generic_and_rollback_preserves_proofs(db, world, password):
    raw, proof_id = issue(db, world)
    _, sibling_id = issue(db, world)
    with pytest.raises(InvalidResetPassword) as error:
        confirm_reset(db, raw, password)
    assert str(error.value) == "Password does not meet the creation policy"
    assert_rolled_back(db, world, [proof_id, sibling_id])


def test_email_change_invalidates_old_bound_proof_before_kdf(db, world, monkeypatch):
    raw, proof_id = issue(db, world)
    db.get(VisitorAccount, world["account_id"]).email_canonical = "new-contact@example.com"
    db.commit()
    monkeypatch.setattr(visitor_passwords, "hash_password", lambda *_: pytest.fail("Old email proof reached KDF"))
    with pytest.raises(InvalidResetProof):
        confirm_reset(db, raw, NEW)
    assert_rolled_back(db, world, [proof_id])


def test_full_unicode_password_is_preserved_without_using_crm_bcrypt(db, world, monkeypatch):
    from app.api.core import security
    monkeypatch.setattr(security, "get_password_hash", lambda *_: pytest.fail("Reset called CRM bcrypt"))
    raw, _ = issue(db, world)
    password = " " + "A!b7Жю" * 20 + " "
    confirm_reset(db, raw, password)
    db.commit()
    encoded = db.get(VisitorAccount, world["account_id"]).hashed_password
    assert visitor_passwords.verify_password(password, encoded)
    assert not visitor_passwords.verify_password(password.strip(), encoded)


@pytest.mark.parametrize("stage", ["kdf_failure", "expiry_after_kdf", "expiry_after_revocation"])
def test_late_failures_require_whole_rollback(db, world, monkeypatch, stage):
    raw, proof_id = issue(db, world)
    _, sibling_id = issue(db, world)
    expiry = db.get(VisitorChallenge, proof_id).expires_at
    if stage in {"kdf_failure", "expiry_after_kdf"}:
        original = visitor_passwords.hash_password
        def hash_password(password):
            if stage == "kdf_failure":
                raise RuntimeError("Synthetic KDF failure")
            result = original(password)
            world["clock"][0] = expiry
            return result
        monkeypatch.setattr(visitor_passwords, "hash_password", hash_password)
    else:
        original = visitor_sessions.revoke_all_sessions
        def revoke(*args, **kwargs):
            result = original(*args, **kwargs)
            world["clock"][0] = expiry
            return result
        monkeypatch.setattr(visitor_sessions, "revoke_all_sessions", revoke)
    with pytest.raises(RuntimeError if stage == "kdf_failure" else InvalidResetProof):
        confirm_reset(db, raw, NEW)
    assert_rolled_back(db, world, [proof_id, sibling_id])


def test_deferred_commit_failure_restores_password_proofs_and_sessions(db, world):
    raw, proof_id = issue(db, world)
    _, sibling_id = issue(db, world)
    db.execute(text("""CREATE FUNCTION review_reset_commit_failure() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'synthetic deferred reset failure'; RETURN NEW; END $$"""))
    db.execute(text("""CREATE CONSTRAINT TRIGGER review_reset_commit_failure
        AFTER UPDATE ON visitor_accounts DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION review_reset_commit_failure()"""))
    db.commit()
    try:
        confirm_reset(db, raw, NEW)
        with pytest.raises(DBAPIError):
            db.commit()
        assert_rolled_back(db, world, [proof_id, sibling_id])
    finally:
        db.rollback()
        db.execute(text("DROP TRIGGER review_reset_commit_failure ON visitor_accounts"))
        db.execute(text("DROP FUNCTION review_reset_commit_failure()"))
        db.commit()


@contextmanager
def account_wait(engine):
    entering, seen = Event(), {}
    def capture(conn, cursor, statement, parameters, context, executemany):
        if "visitor_accounts" in statement.lower() and "for update" in statement.lower():
            seen["pid"] = conn.connection.driver_connection.get_backend_pid()
            entering.set()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        def wait(holder_pid):
            assert entering.wait(timeout=5)
            blocked = False
            end = time.monotonic() + 5
            with engine.connect() as observer:
                while time.monotonic() < end:
                    if holder_pid in observer.execute(text("SELECT pg_blocking_pids(:pid)"), {"pid": seen["pid"]}).scalar_one():
                        blocked = True
                        break
                    time.sleep(0.01)
            assert blocked
        yield wait
    finally:
        event.remove(engine, "before_cursor_execute", capture)


def run_reset(engine, raw, password=NEW, barrier=None):
    with Session(engine, autoflush=False) as session:
        try:
            if barrier:
                barrier.wait(timeout=10)
            result = confirm_reset(session, raw, password)
            session.commit()
            return "reset", result.revoked_sessions
        except (InvalidResetProof, InvalidResetPassword) as error:
            session.rollback()
            return type(error).__name__, 0


def test_reset_before_waiting_old_password_login_refuses_the_old_password(db, world, client):
    raw, _ = issue(db, world)
    engine = db.get_bind()
    with Session(engine, autoflush=False) as holder:
        confirm_reset(holder, raw, NEW)
        holder_pid = holder.execute(text("SELECT pg_backend_pid()")).scalar_one()
        with account_wait(engine) as wait:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(client.post, "/visitor/auth/login", json={"email": "reset.owner@example.com", "password": OLD})
                try:
                    wait(holder_pid)
                    holder.commit()
                    response = future.result(timeout=15)
                finally:
                    holder.rollback()
    assert response.status_code == 401
    assert db.query(VisitorSession).count() == 3
    assert all(visitor_sessions.get_valid_session(db, token) is None for token in world["raws"][:2])


def test_login_before_waiting_reset_is_revoked_including_its_new_session(db, world):
    raw, _ = issue(db, world)
    engine = db.get_bind()
    with Session(engine, autoflush=False) as holder:
        account = visitor_sessions.lock_verified_account(holder, world["account_id"])
        assert visitor_passwords.verify_password(OLD, account.hashed_password)
        _, just_issued = visitor_sessions.create_session(holder, account)
        holder_pid = holder.execute(text("SELECT pg_backend_pid()")).scalar_one()
        with account_wait(engine) as wait:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(run_reset, engine, raw)
                try:
                    wait(holder_pid)
                    holder.commit()
                    outcome = future.result(timeout=15)
                finally:
                    holder.rollback()
    assert outcome == ("reset", 3)
    assert visitor_sessions.get_valid_session(db, just_issued) is None
    assert db.query(VisitorSession).count() == 4


def test_new_reset_issue_after_reset_commit_survives_sibling_invalidation(db, world):
    raw, proof_id = issue(db, world)
    engine = db.get_bind()
    def issue_after():
        with Session(engine, autoflush=False) as waiter:
            issued = visitor_challenges.issue_challenge(waiter, "reset", account_id=world["account_id"], email="reset.owner@example.com")
            row_id, token = issued.row.id, issued.raw_token
            waiter.commit()
            return row_id, token
    with Session(engine, autoflush=False) as holder:
        confirm_reset(holder, raw, NEW)
        holder_pid = holder.execute(text("SELECT pg_backend_pid()")).scalar_one()
        with account_wait(engine) as wait:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(issue_after)
                try:
                    wait(holder_pid)
                    holder.commit()
                    new_id, new_raw = future.result(timeout=15)
                finally:
                    holder.rollback()
    assert db.get(VisitorChallenge, proof_id).consumed_at is not None
    assert db.get(VisitorChallenge, new_id).consumed_at is None
    confirm_reset(db, new_raw, "Next!Visitor-47-Pass")
    db.commit()


def test_racing_reset_proofs_allow_only_one_password_change(db, world):
    first, _ = issue(db, world)
    second, _ = issue(db, world)
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run_reset, db.get_bind(), token, password, barrier)
                   for token, password in [(first, NEW), (second, "Next!Visitor-47-Pass")]]
        results = [future.result(timeout=20) for future in futures]
    assert sorted(result[0] for result in results) == ["InvalidResetProof", "reset"]
    assert db.query(VisitorChallenge).filter(VisitorChallenge.consumed_at.is_(None)).count() == 0
    assert db.query(VisitorSession).count() == 3
