"""Reset capture issues only current account-bound proofs, after commit."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
from threading import Barrier, Event
import time

import pytest
from sqlalchemy import event, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.core import notification_transport, visitor_challenges, visitor_sessions
from app.api.core import visitor_reset_mail as mail
from app.api.core.config import settings
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.user import User
from app.db.models.visitor_account import VisitorAccount, VisitorGoogleIdentity
from app.db.models.visitor_auth import VisitorChallenge, VisitorSession

NOW = datetime(2026, 10, 26, 12)
EMAIL = "recipient+tag@example.com"


@pytest.fixture(autouse=True)
def capture_mode(monkeypatch):
    clock = [NOW]
    monkeypatch.setattr(settings, "visitor_auth_mail_mode", "capture")
    monkeypatch.setattr(settings, "environment", "test")
    monkeypatch.setattr(settings, "frontend_url", "https://visitor.example")
    monkeypatch.setattr(mail, "utcnow", lambda: clock[0])
    monkeypatch.setattr(visitor_challenges, "utcnow", lambda: clock[0])
    def forbidden(*_args, **_kwargs):
        pytest.fail("Reset capture entered a live notification transport")
    monkeypatch.setattr(notification_transport, "get_notification_transport", forbidden)
    mail.clear_capture()
    yield clock
    mail.clear_capture()


@pytest.fixture
def account(db):
    row = VisitorAccount(email_canonical=EMAIL, email_verified_at=NOW, hashed_password="existing-hash")
    db.add(row)
    db.commit()
    account_id = row.id
    db.rollback()
    return account_id


def token(message):
    return message.text.split("#token=", 1)[1].splitlines()[0]


def committed(db, email=EMAIL, locale="ru"):
    message = mail.prepare(db, email, locale)
    db.commit()
    if message is not None:
        mail.capture_after_commit(db, message)
    return message


@pytest.mark.parametrize("mode,environment", [("disabled", "test"), ("capture", "production"),
    ("capture", "staging"), ("live", "development")])
def test_gate_rejects_before_database_or_connection(db, monkeypatch, mode, environment):
    monkeypatch.setattr(settings, "visitor_auth_mail_mode", mode)
    monkeypatch.setattr(settings, "environment", environment)
    monkeypatch.setattr(db, "query", lambda *_args, **_kwargs: pytest.fail("disabled request queried DB"))
    monkeypatch.setattr(db, "get_bind", lambda *_args, **_kwargs: pytest.fail("disabled capture opened connection"))
    with pytest.raises(mail.ResetMailUnavailable):
        mail.prepare(db, EMAIL, "en")
    prepared = mail.PreparedResetMail(1, "a" * 64, 1, EMAIL, "subject", "secret")
    with pytest.raises(mail.ResetMailUnavailable):
        mail.capture_after_commit(db, prepared)
    assert mail.captured_messages() == ()


@pytest.mark.parametrize("base", ["https://user:secret@visitor.example", "https://visitor.example/path",
    "https://visitor.example?callback=evil", "https://visitor.example#fragment", " https://visitor.example",
    "javascript:alert(1)", "https://visitor.example:invalid", ""])
def test_bad_frontend_configuration_never_queries_db(db, monkeypatch, base):
    monkeypatch.setattr(settings, "frontend_url", base)
    monkeypatch.setattr(db, "query", lambda *_args, **_kwargs: pytest.fail("bad config queried DB"))
    with pytest.raises(mail.ResetMailUnavailable):
        mail.prepare(db, EMAIL, "en")


@pytest.mark.parametrize("locale", ["en", "ru", "cz", "unsupported"])
def test_bound_proof_fragment_and_private_repr_without_other_mutations(db, account, monkeypatch, tmp_path, caplog, locale):
    file = tmp_path / "global-capture.jsonl"
    monkeypatch.setattr(settings, "capture_mail_file", str(file))
    original = db.get(VisitorAccount, account)
    visitor_sessions.create_session(db, original)
    db.add(VisitorGoogleIdentity(subject="existing-subject", account_id=account))
    db.commit()
    before = [db.query(model).count() for model in (User, VisitorAccount, VisitorSession, VisitorGoogleIdentity, NotificationOutbox)]
    message = committed(db, "  Recipient+Tag@EXAMPLE.COM  ", locale)
    raw = token(message)
    assert message.recipient == EMAIL and message.account_id == account
    assert "https://visitor.example/visitor/reset-password#token=" + raw in message.text
    assert raw not in repr(message) and EMAIL not in repr(message)
    assert not file.exists() and raw not in caplog.text
    row = db.query(VisitorChallenge).one()
    assert row.purpose == "reset" and row.account_id == account and row.email_canonical == EMAIL
    assert row.token_hash == hashlib.sha256(raw.encode()).hexdigest()
    assert row.expires_at - row.created_at == timedelta(minutes=30)
    assert raw not in [getattr(row, column.name) for column in row.__table__.columns]
    assert before == [db.query(model).count() for model in (User, VisitorAccount, VisitorSession, VisitorGoogleIdentity, NotificationOutbox)]
    assert db.get(VisitorAccount, account).hashed_password == "existing-hash"
    assert db.query(VisitorSession).one().revoked_at is None


@pytest.mark.parametrize("identity", ["unknown", "crm", "google"])
def test_only_visitor_identity_can_receive_a_reset_but_google_only_is_allowed(db, make_user, identity):
    if identity == "crm":
        make_user(email=EMAIL)
    elif identity == "google":
        row = VisitorAccount(email_canonical=EMAIL, email_verified_at=NOW)
        db.add(row)
        db.flush()
        db.add(VisitorGoogleIdentity(subject="google-only", account_id=row.id))
        db.commit()
    message = committed(db)
    assert (message is not None) == (identity == "google")
    assert db.query(VisitorChallenge).count() == (1 if identity == "google" else 0)
    assert len(mail.captured_messages()) == (1 if identity == "google" else 0)
    if identity == "google":
        assert db.query(VisitorAccount).one().hashed_password is None


def test_account_cooldown_cap_does_not_consume_old_proofs_or_count_other_purposes(db, account, capture_mode):
    other = visitor_challenges.issue_challenge(db, "email_change", account_id=account, email="next@example.com")
    other_id = other.row.id
    db.commit()
    for _ in range(5):
        assert committed(db) is not None
        assert committed(db) is None
        capture_mode[0] += timedelta(seconds=61)
    assert committed(db) is None
    assert db.query(VisitorChallenge).count() == 6
    assert all(row.consumed_at is None for row in db.query(VisitorChallenge))
    capture_mode[0] = NOW + timedelta(hours=1, seconds=1)
    assert committed(db) is not None
    assert db.get(VisitorChallenge, other_id).purpose == "email_change"


def test_two_concurrent_requests_serialize_on_account_without_advisory_lock(db, account):
    barrier = Barrier(2)
    statements = []
    def record(_conn, _cursor, statement, *_args):
        statements.append(statement.lower())
    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    def request(_):
        with Session(engine) as transaction:
            barrier.wait(timeout=5)
            return committed(transaction) is not None
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(request, range(2)))
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert sorted(results) == [False, True]
    assert not any("pg_advisory" in sql for sql in statements)
    assert db.query(VisitorChallenge).count() == len(mail.captured_messages()) == 1


@pytest.mark.parametrize("change", ["email", "delete", "disable", "clock"])
def test_account_and_configuration_are_rechecked_after_confirmed_account_lock_wait(db, account, capture_mode, monkeypatch, change):
    if change == "clock":
        assert committed(db) is not None
        capture_mode[0] = NOW + timedelta(seconds=59)
    engine = db.get_bind()
    waiting = Event()
    observed = {}
    def query(conn, _cursor, statement, *_args):
        if "visitor_accounts" in statement.lower() and "FOR UPDATE" in statement:
            observed["pid"] = conn.connection.driver_connection.get_backend_pid()
            waiting.set()
    def request():
        with Session(engine) as transaction:
            try:
                return committed(transaction)
            except mail.ResetMailUnavailable:
                transaction.rollback()
                return "disabled"
    with Session(engine) as holder:
        row = visitor_sessions.lock_verified_account(holder, account)
        holder_pid = holder.execute(text("SELECT pg_backend_pid()")).scalar_one()
        event.listen(engine, "before_cursor_execute", query)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(request)
                try:
                    assert waiting.wait(timeout=5)
                    end = time.monotonic() + 5
                    with engine.connect() as observer:
                        while time.monotonic() < end:
                            if holder_pid in observer.execute(text("SELECT pg_blocking_pids(:pid)"), {"pid": observed["pid"]}).scalar_one():
                                break
                            time.sleep(0.01)
                        else:
                            pytest.fail("PostgreSQL did not confirm Account lock wait")
                    if change == "email":
                        row.email_canonical = "new@example.com"
                    elif change == "delete":
                        holder.delete(row)
                    elif change == "disable":
                        monkeypatch.setattr(settings, "visitor_auth_mail_mode", "disabled")
                    else:
                        capture_mode[0] = NOW + timedelta(seconds=60)
                    holder.commit()
                finally:
                    holder.rollback()
                result = future.result(timeout=10)
        finally:
            event.remove(engine, "before_cursor_execute", query)
    assert result == "disabled" if change == "disable" else (result is not None if change == "clock" else result is None)
    db.expire_all()
    assert db.query(VisitorChallenge).count() == (2 if change == "clock" else 0)
    if change == "clock":
        assert db.query(VisitorChallenge).order_by(VisitorChallenge.id.desc()).first().created_at == NOW + timedelta(seconds=60)


def test_capture_requires_commit_and_rejects_rollback_or_active_transaction(db, account):
    message = mail.prepare(db, EMAIL, "en")
    with pytest.raises(mail.ResetMailUnavailable):
        mail.capture_after_commit(db, message)
    db.rollback()
    with pytest.raises(mail.ResetMailUnavailable):
        mail.capture_after_commit(db, message)
    message = mail.prepare(db, EMAIL, "en")
    db.commit()
    db.query(VisitorChallenge).count()
    with pytest.raises(mail.ResetMailUnavailable):
        mail.capture_after_commit(db, message)
    db.rollback()
    mail.capture_after_commit(db, message)
    mail.capture_after_commit(db, message)
    assert len(mail.captured_messages()) == 1


@pytest.mark.parametrize("change", ["expired", "consumed", "account_email", "account_delete", "disabled", "foreign_account", "recipient", "digest"])
def test_capture_refuses_stale_or_mismatched_committed_proof(db, account, capture_mode, monkeypatch, change):
    message = mail.prepare(db, EMAIL, "en")
    db.commit()
    if change == "expired":
        capture_mode[0] = NOW + timedelta(minutes=30)
    elif change == "consumed":
        db.execute(update(VisitorChallenge).where(VisitorChallenge.id == message.challenge_id).values(consumed_at=NOW))
        db.commit()
    elif change == "account_email":
        db.execute(update(VisitorAccount).where(VisitorAccount.id == account).values(email_canonical="next@example.com"))
        db.commit()
    elif change == "account_delete":
        db.delete(db.get(VisitorAccount, account))
        db.commit()
    elif change == "disabled":
        monkeypatch.setattr(settings, "visitor_auth_mail_mode", "disabled")
    elif change == "foreign_account":
        message = replace(message, account_id=account + 100)
    elif change == "recipient":
        message = replace(message, recipient="foreign@example.com")
    else:
        message = replace(message, challenge_hash="a" * 64)
    with pytest.raises(mail.ResetMailUnavailable):
        mail.capture_after_commit(db, message)
    assert mail.captured_messages() == ()


def test_deferred_commit_failure_never_captures_or_persists_proof(db, account):
    db.execute(text("""CREATE FUNCTION reset_capture_commit_failure() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'synthetic reset capture commit failure'; RETURN NEW; END $$"""))
    db.execute(text("""CREATE CONSTRAINT TRIGGER reset_capture_commit_failure
        AFTER INSERT ON visitor_challenges DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION reset_capture_commit_failure()"""))
    db.commit()
    try:
        message = mail.prepare(db, EMAIL, "en")
        with pytest.raises(DBAPIError):
            db.commit()
        db.rollback()
        with pytest.raises(mail.ResetMailUnavailable):
            mail.capture_after_commit(db, message)
        assert db.query(VisitorChallenge).count() == 0
        assert mail.captured_messages() == ()
    finally:
        db.rollback()
        db.execute(text("DROP TRIGGER reset_capture_commit_failure ON visitor_challenges"))
        db.execute(text("DROP FUNCTION reset_capture_commit_failure()"))
        db.commit()


def test_global_live_flags_do_not_enable_delivery_and_capture_is_bounded(db, monkeypatch):
    monkeypatch.setattr(settings, "notification_transport", "live")
    monkeypatch.setattr(settings, "email_provider", "gmail")
    monkeypatch.setattr(settings, "gmail_delivery_enabled", True)
    monkeypatch.setattr(settings, "pilot_email_delivery_enabled", True)
    for number in range(66):
        email = f"recipient{number}@example.com"
        db.add(VisitorAccount(email_canonical=email, email_verified_at=NOW))
        db.commit()
        assert committed(db, email) is not None
    messages = mail.captured_messages()
    assert len(messages) == 64
    assert messages[0].recipient == "recipient2@example.com"
    assert messages[-1].recipient == "recipient65@example.com"
