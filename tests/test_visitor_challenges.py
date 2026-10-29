"""Challenge ownership and consumption need real transactions and row locks."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
from threading import Barrier, Event, current_thread

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.core.visitor_challenges import consume_challenge, issue_challenge
from app.db.models.visitor_account import VisitorAccount
from app.db.models.visitor_auth import VisitorChallenge, VisitorSession

NOW = datetime(2026, 10, 25)
ORIGIN = "https://xtrix.example"


@pytest.fixture
def accounts(db):
    first = VisitorAccount(email_canonical="first@example.com", email_verified_at=NOW, hashed_password="original-hash")
    second = VisitorAccount(email_canonical="second@example.com", email_verified_at=NOW)
    db.add_all([first, second])
    db.flush()
    ids = first.id, second.id
    db.commit()
    return ids


def issue(db, purpose, owner_id):
    kwargs = {}
    if purpose in {"reset", "email_change", "google_link"}:
        kwargs["account_id"] = owner_id
    if purpose in {"register", "reset", "email_change"}:
        kwargs["email"] = "new@example.com" if purpose == "email_change" else "first@example.com"
    else:
        kwargs["origin"] = ORIGIN
    issued = issue_challenge(db, purpose, now=NOW, **kwargs)
    result = issued.raw_token, issued.row.id
    db.commit()
    return result


def consume(db, token, purpose, owner_id, **changes):
    kwargs = {"purpose": purpose, "now": NOW + timedelta(seconds=1)}
    if purpose in {"email_change", "google_link"}:
        kwargs["account_id"] = owner_id
    if purpose in {"google_login", "google_link"}:
        kwargs["origin"] = ORIGIN
    return consume_challenge(db, token, **{**kwargs, **changes})


@pytest.mark.parametrize("purpose", ["register", "reset", "email_change", "google_login", "google_link"])
def test_issue_uses_random_prefixed_proofs_stores_only_hash_and_sets_purpose_ttl(db, accounts, purpose):
    token, row_id = issue(db, purpose, accounts[0])
    row = db.get(VisitorChallenge, row_id)
    assert token.startswith("vch_") and len(token) == 47
    assert row.token_hash == hashlib.sha256(token.encode("ascii")).hexdigest()
    assert row.expires_at == NOW + timedelta(minutes=5 if purpose.startswith("google_") else 30)
    assert token not in {str(getattr(row, column.name)) for column in VisitorChallenge.__table__.columns}
    other_token, _ = issue(db, purpose, accounts[0])
    assert other_token != token
    result = consume(db, token, purpose, accounts[0])
    assert result is not None and result.id == row_id
    assert result.consumed_at == NOW + timedelta(seconds=1)
    db.commit()
    assert consume(db, token, purpose, accounts[0]) is None


def test_issue_flushes_but_does_not_commit_or_log_the_raw_proof(db):
    issued = issue_challenge(db, "register", email="  Straße@EXAMPLE.COM  ", now=NOW)
    row_id = issued.row.id
    assert row_id is not None and issued.row.email_canonical == "strasse@example.com"
    assert issued.raw_token not in repr(issued)
    with Session(db.get_bind()) as observer:
        assert observer.get(VisitorChallenge, row_id) is None
    db.rollback()
    assert db.get(VisitorChallenge, row_id) is None


@pytest.mark.parametrize("purpose", ["register", "reset", "email_change", "google_login", "google_link"])
def test_exact_expiry_is_invalid_and_does_not_burn_the_proof(db, accounts, purpose):
    token, row_id = issue(db, purpose, accounts[0])
    expiry = NOW + timedelta(minutes=5 if purpose.startswith("google_") else 30)
    assert consume(db, token, purpose, accounts[0], now=expiry) is None
    assert db.get(VisitorChallenge, row_id).consumed_at is None
    # Move only the trusted test clock back, not the stored expiry.
    assert consume(db, token, purpose, accounts[0], now=expiry - timedelta(microseconds=1)) is not None


def test_aware_test_clock_is_normalized_to_naive_utc(db):
    local = datetime(2026, 10, 25, 2, tzinfo=timezone(timedelta(hours=2)))
    issued = issue_challenge(db, "register", email="guest@example.com", now=local)
    assert issued.row.created_at == NOW
    assert issued.row.expires_at == NOW + timedelta(minutes=30)


@pytest.mark.parametrize("malformed", [None, "", "vss_" + "A" * 43, "vch_" + "A" * 42,
    "vch_" + "A" * 42 + "B", " vch_" + "A" * 43, "vch_" + "A" * 43 + "\n"])
def test_malformed_or_other_namespace_tokens_are_not_accepted(db, malformed):
    assert consume_challenge(db, malformed, purpose="register", now=NOW) is None


def test_wrong_purpose_does_not_consume_a_valid_proof(db, accounts):
    token, row_id = issue(db, "register", accounts[0])
    assert consume(db, token, "reset", accounts[0]) is None
    assert db.get(VisitorChallenge, row_id).consumed_at is None
    assert consume(db, token, "register", accounts[0]) is not None


@pytest.mark.parametrize("purpose", ["reset", "email_change", "google_link"])
def test_wrong_account_does_not_burn_or_retarget_proof(db, accounts, purpose):
    token, row_id = issue(db, purpose, accounts[0])
    original_email = db.get(VisitorChallenge, row_id).email_canonical
    assert consume(db, token, purpose, accounts[0], account_id=accounts[1]) is None
    row = db.get(VisitorChallenge, row_id)
    assert row.consumed_at is None and row.account_id == accounts[0] and row.email_canonical == original_email
    assert consume(db, token, purpose, accounts[0]) is not None


@pytest.mark.parametrize("purpose", ["email_change", "google_link"])
def test_authenticated_binding_is_mandatory_for_email_change_and_google_link(db, accounts, purpose):
    token, row_id = issue(db, purpose, accounts[0])
    assert consume(db, token, purpose, accounts[0], account_id=None) is None
    assert db.get(VisitorChallenge, row_id).consumed_at is None
    assert consume(db, token, purpose, accounts[0]) is not None


@pytest.mark.parametrize("purpose", ["google_login", "google_link"])
@pytest.mark.parametrize("origin", [None, "https://different.example", "https://xtrix.example/path", " "])
def test_wrong_or_missing_origin_does_not_consume_google_proof(db, accounts, purpose, origin):
    token, row_id = issue(db, purpose, accounts[0])
    assert consume(db, token, purpose, accounts[0], origin=origin) is None
    assert db.get(VisitorChallenge, row_id).consumed_at is None
    assert consume(db, token, purpose, accounts[0]) is not None


@pytest.mark.parametrize("purpose,kwargs", [
    ("register", {"email": "first@example.com", "account_id": 1}),
    ("register", {"email": "first@example.com", "origin": ORIGIN}),
    ("reset", {"email": "first@example.com"}),
    ("email_change", {"email": "new@example.com"}),
    ("google_link", {"origin": ORIGIN}),
    ("google_login", {"origin": ORIGIN, "email": "claimed@example.com"}),
    ("google_login", {"origin": "https://xtrix.example/#secret"}),
    ("unknown", {"email": "first@example.com"}),
])
def test_issue_refuses_invalid_target_shapes_without_creating_rows(db, purpose, kwargs):
    with pytest.raises(ValueError):
        issue_challenge(db, purpose, now=NOW, **kwargs)
    assert db.query(VisitorChallenge).count() == 0


def test_existing_registration_target_is_never_overwritten_or_linked_by_the_core(db, accounts):
    token, _ = issue(db, "register", accounts[0])
    row = consume(db, token, "register", accounts[0])
    assert row is not None and row.account_id is None
    db.commit()
    owner = db.get(VisitorAccount, accounts[0])
    assert owner.email_canonical == "first@example.com" and owner.hashed_password == "original-hash"
    assert db.query(VisitorAccount).count() == 2


def test_reset_rejects_the_old_email_snapshot_even_with_a_stale_cached_account(db, accounts):
    token, row_id = issue(db, "reset", accounts[0])
    owner = db.get(VisitorAccount, accounts[0])
    assert owner.email_canonical == "first@example.com"
    with Session(db.get_bind()) as other:
        other.execute(update(VisitorAccount).where(VisitorAccount.id == accounts[0]).values(email_canonical="changed@example.com"))
        other.commit()
    assert owner.email_canonical == "first@example.com"
    assert consume(db, token, "reset", accounts[0]) is None
    assert db.get(VisitorChallenge, row_id).consumed_at is None
    assert db.get(VisitorAccount, accounts[0]).email_canonical == "changed@example.com"


def test_consume_reloads_a_previously_cached_challenge(db, accounts):
    token, row_id = issue(db, "reset", accounts[0])
    cached = db.get(VisitorChallenge, row_id)
    assert cached.consumed_at is None
    with Session(db.get_bind()) as other:
        assert consume(other, token, "reset", accounts[0]) is not None
        other.commit()
    assert cached.consumed_at is None
    assert consume(db, token, "reset", accounts[0]) is None
    assert cached.consumed_at == NOW + timedelta(seconds=1)


def test_failed_dependent_account_and_session_write_rolls_consumption_back(db, accounts):
    token, row_id = issue(db, "reset", accounts[0])
    assert consume(db, token, "reset", accounts[0]) is not None
    db.get(VisitorAccount, accounts[0]).hashed_password = "new-hash"
    db.add(VisitorSession(account_id=99999, token_hash="a" * 64, created_at=NOW,
        expires_at=NOW + timedelta(days=30)))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    assert db.get(VisitorChallenge, row_id).consumed_at is None
    assert db.get(VisitorAccount, accounts[0]).hashed_password == "original-hash"
    assert db.query(VisitorSession).count() == 0
    assert consume(db, token, "reset", accounts[0]) is not None


@pytest.mark.parametrize("purpose", ["register", "reset"])
def test_two_real_concurrent_consumers_have_only_one_winner(db, accounts, purpose):
    token, row_id = issue(db, purpose, accounts[0])
    bind = db.get_bind()
    barrier = Barrier(2)
    def run(_):
        with Session(bind) as session:
            barrier.wait(timeout=5)
            row = consume(session, token, purpose, accounts[0])
            won = row is not None
            session.commit()
            return won
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(run, range(2)))
    assert sorted(results) == [False, True]
    db.expire_all()
    assert db.get(VisitorChallenge, row_id).consumed_at == NOW + timedelta(seconds=1)


def test_a_consumer_waiting_for_account_does_not_lock_the_challenge_first(db, accounts):
    token, row_id = issue(db, "reset", accounts[0])
    bind = db.get_bind()
    attempted = Event()
    def mark_account_attempt(_connection, _cursor, statement, _parameters, _context, _many):
        if current_thread().name.startswith("visitor-lock-order") and "visitor_accounts" in statement and "FOR UPDATE" in statement:
            attempted.set()
    event.listen(bind, "before_cursor_execute", mark_account_attempt)
    blocker = Session(bind)
    try:
        blocker.execute(select(VisitorAccount).where(VisitorAccount.id == accounts[0]).with_for_update()).scalar_one()
        def run():
            with Session(bind) as session:
                row = consume(session, token, "reset", accounts[0])
                result = row.id if row is not None else None
                session.commit()
                return result
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="visitor-lock-order") as executor:
            future = executor.submit(run)
            try:
                assert attempted.wait(timeout=5)
                with Session(bind) as observer:
                    # If the consumer took Challenge first, this fails immediately with lock_not_available.
                    row = observer.execute(select(VisitorChallenge).where(VisitorChallenge.id == row_id)
                                           .with_for_update(nowait=True)).scalar_one()
                    assert row.consumed_at is None
            finally:
                blocker.rollback()
            assert future.result(timeout=5) == row_id
    finally:
        blocker.rollback()
        blocker.close()
        event.remove(bind, "before_cursor_execute", mark_account_attempt)


@pytest.mark.parametrize("purpose", ["register", "reset"])
def test_proof_expiring_while_waiting_for_a_lock_is_not_consumed(db, accounts, monkeypatch, purpose):
    from app.api.core import visitor_challenges
    token, row_id = issue(db, purpose, accounts[0])
    deadline = db.get(VisitorChallenge, row_id).expires_at
    clock = [NOW]
    monkeypatch.setattr(visitor_challenges, "utcnow", lambda: clock[0])
    attempted = Event()
    bind = db.get_bind()
    lock_table = "visitor_accounts" if purpose == "reset" else "visitor_challenges"
    def mark_attempt(_connection, _cursor, statement, _parameters, _context, _many):
        if current_thread().name.startswith("visitor-deadline") and lock_table in statement and "FOR UPDATE" in statement:
            attempted.set()
    event.listen(bind, "before_cursor_execute", mark_attempt)
    try:
        if purpose == "reset":
            db.execute(select(VisitorAccount).where(VisitorAccount.id == accounts[0]).with_for_update()).scalar_one()
        else:
            db.execute(select(VisitorChallenge).where(VisitorChallenge.id == row_id).with_for_update()).scalar_one()
        def consume_after_wait():
            with Session(bind) as other:
                result = consume_challenge(other, token, purpose=purpose)
                won = result is not None
                other.commit()
                return won
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="visitor-deadline") as executor:
            pending = executor.submit(consume_after_wait)
            try:
                assert attempted.wait(timeout=5)
                clock[0] = deadline
                db.commit()
            finally:
                if db.in_transaction():
                    db.rollback()
            assert pending.result(timeout=10) is False
        db.expire_all()
        assert db.get(VisitorChallenge, row_id).consumed_at is None
    finally:
        db.rollback()
        event.remove(bind, "before_cursor_execute", mark_attempt)


def test_challenge_and_session_credentials_cannot_be_replayed_in_each_others_namespace(db, accounts, monkeypatch):
    from app.api.core import visitor_sessions
    monkeypatch.setattr(visitor_sessions, "utcnow", lambda: NOW)
    owner = visitor_sessions.lock_verified_account(db, accounts[0])
    session, session_raw = visitor_sessions.create_session(db, owner)
    session_id = session.id
    proof = issue_challenge(db, "register", email="fresh@example.com", now=NOW)
    proof_id = proof.row.id
    db.commit()
    assert visitor_sessions.get_valid_session(db, proof.raw_token) is None
    assert consume_challenge(db, session_raw, purpose="register", now=NOW) is None
    assert db.get(VisitorChallenge, proof_id).consumed_at is None
    assert visitor_sessions.get_valid_session(db, session_raw).id == session_id
    assert consume_challenge(db, proof.raw_token, purpose="register", now=NOW).id == proof_id


def test_reset_proof_password_and_all_session_revocations_commit_or_roll_back_together(db, accounts, monkeypatch):
    from app.api.core import visitor_sessions
    from app.api.core.security import get_password_hash, verify_password
    monkeypatch.setattr(visitor_sessions, 'utcnow', lambda: NOW)
    owner_id = accounts[0]
    owner = visitor_sessions.lock_verified_account(db, owner_id)
    owner.hashed_password = get_password_hash('Original!Visitor-pass-26')
    db.commit()
    owner = visitor_sessions.lock_verified_account(db, owner_id)
    first, first_raw = visitor_sessions.create_session(db, owner)
    second, second_raw = visitor_sessions.create_session(db, owner)
    db.commit()
    first_id, second_id, digest = first.id, second.id, first.token_hash
    raw, proof_id = issue(db, 'reset', owner_id)
    assert consume(db, raw, 'reset', owner_id) is not None
    db.get(VisitorAccount, owner_id).hashed_password = get_password_hash('Replacement!Visitor-pass-26')
    assert visitor_sessions.revoke_all_sessions(db, account_id=owner_id) == 2
    db.add(VisitorSession(account_id=owner_id, token_hash=digest, created_at=NOW,
                         expires_at=NOW + timedelta(days=30)))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()
    assert db.get(VisitorChallenge, proof_id).consumed_at is None
    assert verify_password('Original!Visitor-pass-26', db.get(VisitorAccount, owner_id).hashed_password)
    assert visitor_sessions.get_valid_session(db, first_raw).id == first_id
    assert visitor_sessions.get_valid_session(db, second_raw).id == second_id
    assert consume(db, raw, 'reset', owner_id) is not None
    db.get(VisitorAccount, owner_id).hashed_password = get_password_hash('Replacement!Visitor-pass-26')
    assert visitor_sessions.revoke_all_sessions(db, account_id=owner_id) == 2
    db.commit()
    assert verify_password('Replacement!Visitor-pass-26', db.get(VisitorAccount, owner_id).hashed_password)
    assert visitor_sessions.get_valid_session(db, first_raw) is None
    assert visitor_sessions.get_valid_session(db, second_raw) is None
    assert consume(db, raw, 'reset', owner_id) is None


def test_account_bound_proofs_refuse_dirty_account_state_without_discarding_it(db, accounts):
    owner_id = accounts[0]
    raw, proof_id = issue(db, 'reset', owner_id)
    owner = db.get(VisitorAccount, owner_id)
    owner.hashed_password = 'pending-password-change'
    with pytest.raises(ValueError):
        consume(db, raw, 'reset', owner_id)
    assert owner.hashed_password == 'pending-password-change'
    assert db.get(VisitorChallenge, proof_id).consumed_at is None
    with pytest.raises(ValueError):
        issue_challenge(db, 'reset', account_id=owner_id, email='first@example.com', now=NOW)
    assert owner.hashed_password == 'pending-password-change'
    assert db.query(VisitorChallenge).count() == 1
    db.rollback()
    assert db.get(VisitorAccount, owner_id).hashed_password == 'original-hash'
    assert consume(db, raw, 'reset', owner_id) is not None
