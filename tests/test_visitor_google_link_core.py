"""Linking needs the exact recent visitor actor and a separate Google link proof."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import hashlib
from threading import Barrier, Event
import time

import pytest
from sqlalchemy import event, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.core import google_identity, visitor_challenges, visitor_google_credentials
from app.api.core import visitor_google_link, visitor_recent_auth, visitor_sessions
from app.api.core.config import settings
from app.api.core.visitor_recent_auth import VisitorReauthenticationRequired
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.subscription import Subscription
from app.db.models.user import User
from app.db.models.visitor_account import GOOGLE_ISSUER, VisitorAccount, VisitorGoogleIdentity
from app.db.models.visitor_auth import VisitorChallenge, VisitorSession

NOW = datetime(2026, 10, 26, 12)
ORIGIN = "https://visitor.example"


@pytest.fixture
def world(db, monkeypatch):
    clock = [NOW]
    for module in (visitor_sessions, visitor_challenges, visitor_google_credentials,
                   visitor_recent_auth, visitor_google_link):
        monkeypatch.setattr(module, "utcnow", lambda: clock[0])
    monkeypatch.setattr(settings, "frontend_url", ORIGIN)
    monkeypatch.setattr(settings, "cors_origins", "https://alternate.example")
    accounts, sessions, raws = [], [], []
    for email in ["actor@example.com", "other@example.com"]:
        account = VisitorAccount(email_canonical=email, email_verified_at=NOW,
                                 hashed_password="existing-visitor-password-hash")
        db.add(account)
        db.flush()
        session, raw = visitor_sessions.create_session(db, account)
        accounts.append(account.id)
        sessions.append(session.id)
        raws.append(raw)
    db.commit()
    return {"clock": clock, "accounts": accounts, "sessions": sessions, "raws": raws}


def issue(db, world, *, actor=0, purpose="google_link", origin=ORIGIN):
    if purpose == "google_link":
        issued = visitor_google_link.issue_link_challenge(db, world["raws"][actor], origin)
    else:
        issued = visitor_challenges.issue_challenge(db, purpose, origin=origin)
    result = issued.raw_token, issued.row.id
    db.commit()
    return result


def claims(raw, *, subject="candidate-subject", email="google-contact@example.com", expires=None):
    return visitor_google_credentials.VerifiedGoogleClaims(
        issuer=GOOGLE_ISSUER, subject=subject, email=email, issued_at=NOW,
        expires_at=expires or NOW + timedelta(hours=1),
        nonce_hash=hashlib.sha256((raw or "").encode()).hexdigest())


def link(db, world, raw, *, actor=0, verified=None, origin=ORIGIN):
    return visitor_google_link.link(db, world["raws"][actor], raw, origin, verified or claims(raw))


def assert_unused(db, proof_id):
    db.rollback()
    assert db.get(VisitorChallenge, proof_id).consumed_at is None


def test_issue_is_bound_to_actor_origin_and_five_minutes_without_commit(db, world):
    issued = visitor_google_link.issue_link_challenge(db, world["raws"][0], ORIGIN)
    assert issued.row.account_id == world["accounts"][0]
    assert issued.row.purpose == "google_link" and issued.row.origin == ORIGIN
    assert issued.row.expires_at - issued.row.created_at == timedelta(minutes=5)
    assert issued.raw_token not in repr(issued)
    with Session(db.get_bind()) as observer:
        assert observer.query(VisitorChallenge).count() == 0
    db.commit()
    assert db.query(VisitorChallenge).count() == 1


@pytest.mark.parametrize("age,allowed", [
    (timedelta(minutes=5) - timedelta(microseconds=1), True),
    (timedelta(minutes=5), False),
    (timedelta(minutes=5) + timedelta(microseconds=1), False),
    (-timedelta(microseconds=1), False),
])
def test_recent_auth_has_a_strict_five_minute_boundary_and_no_future_time(db, world, age, allowed):
    db.execute(update(VisitorSession).where(VisitorSession.id == world["sessions"][0])
               .values(created_at=NOW - age))
    db.commit()
    if allowed:
        actor = visitor_recent_auth.lock_recent_actor(db, world["raws"][0])
        assert actor.account_id == world["accounts"][0]
    else:
        with pytest.raises(VisitorReauthenticationRequired):
            visitor_recent_auth.lock_recent_actor(db, world["raws"][0])
    db.rollback()


@pytest.mark.parametrize("raw", [None, "", "Bearer synthetic-CRM-token", "vch_" + "A" * 43, "vst_" + "A" * 43])
def test_only_an_actual_visitor_session_can_issue_a_link_proof(db, world, raw):
    with pytest.raises(VisitorReauthenticationRequired):
        visitor_google_link.issue_link_challenge(db, raw, ORIGIN)
    assert db.query(VisitorChallenge).count() == 0


def test_link_writes_only_identity_and_proof_without_changing_account_or_creating_session(db, world, monkeypatch):
    raw, proof_id = issue(db, world)
    tables = (User, Client, Business, Subscription, NotificationOutbox)
    before = [db.query(table).count() for table in tables]
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Link core entered external Google/CRM verification")
    monkeypatch.setattr(google_identity, "verify_credential", forbidden)
    result = link(db, world, raw, verified=claims(raw, email="other@example.com"))
    assert result.created and result.account_id == world["accounts"][0]
    assert raw not in repr(result) and world["raws"][0] not in repr(result)
    with Session(db.get_bind()) as observer:
        assert observer.query(VisitorGoogleIdentity).count() == 0
        assert observer.get(VisitorChallenge, proof_id).consumed_at is None
    db.commit()
    account = db.get(VisitorAccount, world["accounts"][0])
    assert account.email_canonical == "actor@example.com"
    assert account.hashed_password == "existing-visitor-password-hash"
    assert db.query(VisitorGoogleIdentity).one().account_id == account.id
    assert db.query(VisitorAccount).count() == db.query(VisitorSession).count() == 2
    assert [db.query(table).count() for table in tables] == before
    assert all(raw not in [getattr(row, column.name) for column in row.__table__.columns]
               for row in [db.query(VisitorGoogleIdentity).one(), db.get(VisitorChallenge, proof_id)])


def test_own_principal_with_a_new_proof_is_idempotent_but_same_proof_cannot_replay(db, world):
    first, _ = issue(db, world)
    assert link(db, world, first).created
    db.commit()
    second, second_id = issue(db, world)
    assert not link(db, world, second).created
    db.commit()
    assert db.query(VisitorGoogleIdentity).count() == 1
    with pytest.raises(visitor_google_link.InvalidGoogleLink):
        link(db, world, second)
    db.rollback()
    assert db.get(VisitorChallenge, second_id).consumed_at is not None


def test_foreign_principal_is_not_transferred_even_when_google_email_matches_actor(db, world):
    db.add(VisitorGoogleIdentity(subject="candidate-subject", account_id=world["accounts"][1]))
    db.commit()
    raw, proof_id = issue(db, world)
    with Session(db.get_bind()) as holder:
        holder.query(VisitorGoogleIdentity).with_for_update().one()
        db.execute(text("SET LOCAL lock_timeout='500ms'"))
        with pytest.raises(visitor_google_link.GoogleIdentityLinkConflict):
            link(db, world, raw, verified=claims(raw, email="actor@example.com"))
        db.rollback()
        holder.rollback()
    assert db.query(VisitorGoogleIdentity).one().account_id == world["accounts"][1]
    assert db.get(VisitorChallenge, proof_id).consumed_at is None


@pytest.mark.parametrize("kind", ["foreign_actor", "wrong_purpose", "wrong_origin", "wrong_nonce_hash", "expired_jwt"])
def test_separate_actor_nonce_origin_and_purpose_are_all_required(db, world, kind):
    raw, proof_id = issue(db, world, purpose="google_login" if kind == "wrong_purpose" else "google_link")
    verified = claims("vch_" + "A" * 43) if kind == "wrong_nonce_hash" else claims(raw)
    if kind == "expired_jwt":
        verified = claims(raw, expires=NOW)
    with pytest.raises(visitor_google_link.InvalidGoogleLink):
        link(db, world, raw, actor=1 if kind == "foreign_actor" else 0, verified=verified,
             origin="https://foreign.example" if kind == "wrong_origin" else ORIGIN)
    assert_unused(db, proof_id)
    assert db.query(VisitorGoogleIdentity).count() == 0


def test_caller_rollback_restores_link_proof_and_removes_identity(db, world):
    raw, proof_id = issue(db, world)
    assert link(db, world, raw).created
    assert_unused(db, proof_id)
    assert db.query(VisitorGoogleIdentity).count() == 0
    assert link(db, world, raw).created
    db.commit()


def test_real_commit_failure_rolls_back_identity_and_proof(db, world):
    raw, proof_id = issue(db, world)
    db.execute(text("""CREATE FUNCTION review_google_link_commit_failure() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'synthetic deferred link failure'; RETURN NEW; END $$"""))
    db.execute(text("""CREATE CONSTRAINT TRIGGER review_google_link_commit_failure
        AFTER INSERT ON visitor_google_identities DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION review_google_link_commit_failure()"""))
    db.commit()
    try:
        link(db, world, raw)
        with pytest.raises(DBAPIError):
            db.commit()
        assert_unused(db, proof_id)
        assert db.query(VisitorGoogleIdentity).count() == 0
    finally:
        db.rollback()
        db.execute(text("DROP TRIGGER review_google_link_commit_failure ON visitor_google_identities"))
        db.execute(text("DROP FUNCTION review_google_link_commit_failure()"))
        db.commit()


def _run_link(engine, token, raw, verified, barrier=None):
    with Session(engine) as session:
        try:
            if barrier:
                barrier.wait(timeout=10)
            result = visitor_google_link.link(session, token, raw, ORIGIN, verified)
            session.commit()
            return "linked", result.created
        except (visitor_google_link.InvalidGoogleLink, visitor_google_link.GoogleIdentityLinkConflict,
                VisitorReauthenticationRequired) as exc:
            session.rollback()
            return type(exc).__name__, False


def test_same_proof_race_consumes_once(db, world):
    raw, _ = issue(db, world)
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _run_link(db.get_bind(), world["raws"][0], raw, claims(raw), barrier), range(2)))
    assert sorted(row[0] for row in results) == ["InvalidGoogleLink", "linked"]
    assert db.query(VisitorGoogleIdentity).count() == 1


def test_same_actor_different_proofs_are_serialized_and_idempotent(db, world):
    raws = [issue(db, world)[0] for _ in range(2)]
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda raw: _run_link(db.get_bind(), world["raws"][0], raw, claims(raw), barrier), raws))
    assert [row[0] for row in results] == ["linked", "linked"]
    assert sum(row[1] for row in results) == 1
    assert db.query(VisitorGoogleIdentity).count() == 1


def test_two_actors_cannot_link_the_same_principal(db, world):
    raws = [issue(db, world, actor=i)[0] for i in range(2)]
    inserting = Barrier(2)
    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT INTO VISITOR_GOOGLE_IDENTITIES"):
            inserting.wait(timeout=10)
    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_run_link, engine, world["raws"][i], raws[i], claims(raws[i])) for i in range(2)]
            results = [future.result(timeout=15) for future in futures]
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert sorted(row[0] for row in results) == ["GoogleIdentityLinkConflict", "linked"]
    assert db.query(VisitorGoogleIdentity).count() == 1
    assert db.query(VisitorChallenge).filter(VisitorChallenge.consumed_at.is_not(None)).count() == 1
    assert db.query(VisitorSession).count() == 2


@pytest.mark.parametrize("change", ["revoke", "reset", "expire", "future", "stale", "delete"])
def test_actor_is_rechecked_after_waiting_for_account_lock(db, world, monkeypatch, change):
    raw, proof_id = issue(db, world)
    engine = db.get_bind()
    original = visitor_sessions.lock_verified_account
    entering = Event()
    def observe(session, account_id):
        entering.set()
        return original(session, account_id)
    with Session(engine) as holder:
        account = original(holder, world["accounts"][0])
        monkeypatch.setattr(visitor_sessions, "lock_verified_account", observe)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_run_link, engine, world["raws"][0], raw, claims(raw))
            try:
                assert entering.wait(timeout=5)
                if change in {"revoke", "reset"}:
                    if change == "reset":
                        account.hashed_password = "changed-password-hash"
                    visitor_sessions.revoke_all_sessions(holder, account_id=account.id)
                elif change == "expire":
                    holder.execute(update(VisitorSession).where(VisitorSession.id == world["sessions"][0])
                                   .values(expires_at=NOW + timedelta(microseconds=1)))
                    world["clock"][0] += timedelta(microseconds=1)
                elif change == "future":
                    holder.execute(update(VisitorSession).where(VisitorSession.id == world["sessions"][0])
                                   .values(created_at=NOW + timedelta(microseconds=1)))
                elif change == "stale":
                    world["clock"][0] += timedelta(minutes=5)
                else:
                    holder.delete(account)
                holder.commit()
                result = future.result(timeout=10)
            finally:
                holder.rollback()
    assert result[0] == "VisitorReauthenticationRequired"
    db.expire_all()
    proof = db.get(VisitorChallenge, proof_id)
    assert proof is None if change == "delete" else proof.consumed_at is None
    assert db.query(VisitorGoogleIdentity).count() == 0


@pytest.mark.parametrize("deadline", ["valid", "jwt", "proof", "recent", "session"])
def test_final_checks_follow_real_unique_insert_wait(db, world, deadline):
    if deadline == "recent":
        db.execute(update(VisitorSession).where(VisitorSession.id == world["sessions"][0])
                   .values(created_at=NOW - timedelta(minutes=1)))
        db.commit()
    if deadline == "session":
        db.execute(update(VisitorSession).where(VisitorSession.id == world["sessions"][0])
                   .values(expires_at=NOW + timedelta(minutes=1)))
        db.commit()
    raw, proof_id = issue(db, world)
    if deadline == "proof":
        db.execute(update(VisitorChallenge).where(VisitorChallenge.id == proof_id)
                   .values(expires_at=NOW + timedelta(minutes=1)))
        db.commit()
    verified = claims(raw, expires=NOW + timedelta(minutes=1) if deadline == "jwt" else None)
    engine = db.get_bind()
    inserting = Event()
    observed = {}
    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT INTO VISITOR_GOOGLE_IDENTITIES"):
            observed["pid"] = conn.connection.driver_connection.get_backend_pid()
            inserting.set()
    with Session(engine) as holder:
        holder.add(VisitorGoogleIdentity(subject="candidate-subject", account_id=world["accounts"][1]))
        holder.flush()
        holder_pid = holder.execute(text("SELECT pg_backend_pid()")).scalar_one()
        event.listen(engine, "before_cursor_execute", capture)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(_run_link, engine, world["raws"][0], raw, verified)
                try:
                    assert inserting.wait(timeout=10)
                    blocked = False
                    end = time.monotonic() + 5
                    with engine.connect() as observer:
                        while time.monotonic() < end:
                            if holder_pid in observer.execute(text("SELECT pg_blocking_pids(:pid)"), {"pid": observed["pid"]}).scalar_one():
                                blocked = True
                                break
                            time.sleep(0.01)
                    assert blocked and not future.done()
                    advance = timedelta(seconds=1) if deadline == "valid" else timedelta(minutes=4 if deadline == "recent" else 1)
                    world["clock"][0] = NOW + advance
                finally:
                    holder.rollback()
                result = future.result(timeout=10)
        finally:
            event.remove(engine, "before_cursor_execute", capture)
    db.expire_all()
    if deadline == "valid":
        assert result == ("linked", True)
        assert db.query(VisitorGoogleIdentity).one().account_id == world["accounts"][0]
    else:
        expected = "VisitorReauthenticationRequired" if deadline in {"recent", "session"} else "InvalidGoogleLink"
        assert result[0] == expected
        assert db.query(VisitorGoogleIdentity).count() == 0
        assert db.get(VisitorChallenge, proof_id).consumed_at is None


def test_recent_actor_rejects_a_different_session_with_the_same_account_after_wait(db, world, monkeypatch):
    account = db.get(VisitorAccount, world["accounts"][0])
    sibling, _ = visitor_sessions.create_session(db, account)
    sibling_id = sibling.id
    original_digest = db.get(VisitorSession, world["sessions"][0]).token_hash
    db.commit()
    original = visitor_sessions.lock_verified_account
    entering = Event()
    def observe(session, account_id):
        entering.set()
        return original(session, account_id)
    def resolve():
        with Session(db.get_bind()) as waiter:
            try:
                visitor_recent_auth.lock_recent_actor(waiter, world["raws"][0])
                return "accepted"
            except VisitorReauthenticationRequired:
                return "reauth"
            finally:
                waiter.rollback()
    with Session(db.get_bind()) as holder:
        original(holder, world["accounts"][0])
        monkeypatch.setattr(visitor_sessions, "lock_verified_account", observe)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(resolve)
            try:
                assert entering.wait(timeout=5)
                # Defensive contract probe only; no product operation transfers token hashes.
                holder.execute(update(VisitorSession).where(VisitorSession.id == world["sessions"][0])
                    .values(token_hash=hashlib.sha256(b"synthetic-retired-token").hexdigest()))
                holder.execute(update(VisitorSession).where(VisitorSession.id == sibling_id)
                    .values(token_hash=original_digest))
                holder.commit()
                assert future.result(timeout=10) == "reauth"
            finally:
                holder.rollback()
