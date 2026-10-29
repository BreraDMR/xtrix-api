"""Local capture is isolated from every real notification provider and transaction failure."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import hashlib
from threading import Barrier, Event
import time

import pytest
from pydantic import ValidationError
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.core import notification_transport, visitor_challenges, visitor_registration_mail as mail
from app.api.core.config import Settings, settings
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.subscription import Subscription
from app.db.models.user import User
from app.db.models.visitor_account import VisitorAccount, VisitorGoogleIdentity
from app.db.models.visitor_auth import VisitorChallenge, VisitorSession

NOW = datetime(2026, 10, 26, 12)


@pytest.fixture(autouse=True)
def capture_mode(monkeypatch):
    clock = [NOW]
    monkeypatch.setattr(settings, "visitor_auth_mail_mode", "capture")
    monkeypatch.setattr(settings, "environment", "test")
    monkeypatch.setattr(settings, "frontend_url", "https://visitor.example")
    monkeypatch.setattr(mail, "utcnow", lambda: clock[0])
    monkeypatch.setattr(visitor_challenges, "utcnow", lambda: clock[0])
    def forbidden(*_args, **_kwargs):
        pytest.fail("Visitor capture entered the global notification transport")
    monkeypatch.setattr(notification_transport, "get_notification_transport", forbidden)
    mail.clear_capture()
    yield clock
    mail.clear_capture()


def token(message):
    return message.text.split("#token=", 1)[1].splitlines()[0]


def committed_capture(db, email="recipient@example.com", locale="ru"):
    prepared = mail.prepare(db, email, locale)
    db.commit()
    if prepared is not None:
        mail.capture_after_commit(db, prepared)
    return prepared


def test_default_is_disabled_and_live_mode_is_not_part_of_configuration():
    assert Settings.model_fields["visitor_auth_mail_mode"].default == "disabled"
    with pytest.raises(ValidationError):
        Settings(_env_file=None, visitor_auth_mail_mode="live")


@pytest.mark.parametrize("mode,environment", [("disabled", "test"), ("capture", "production"),
    ("capture", "staging"), ("live", "development")])
def test_gate_refuses_before_database_or_capture_connection(db, monkeypatch, mode, environment):
    monkeypatch.setattr(settings, "visitor_auth_mail_mode", mode)
    monkeypatch.setattr(settings, "environment", environment)
    monkeypatch.setattr(db, "execute", lambda *_args, **_kwargs: pytest.fail("disabled request reached DB"))
    monkeypatch.setattr(db, "get_bind", lambda *_args, **_kwargs: pytest.fail("disabled capture opened connection"))
    with pytest.raises(mail.RegistrationMailUnavailable):
        mail.prepare(db, "recipient@example.com", "en")
    prepared = mail.PreparedRegistrationMail(1, "a" * 64, "recipient@example.com", "subject", "private-token")
    with pytest.raises(mail.RegistrationMailUnavailable):
        mail.capture_after_commit(db, prepared)
    assert mail.captured_messages() == ()


@pytest.mark.parametrize("base", ["https://user:secret@visitor.example", "https://visitor.example/path",
    "https://visitor.example?callback=evil", "https://visitor.example#fragment", " https://visitor.example",
    "javascript:alert(1)", "https://visitor.example:invalid", ""])
def test_bad_frontend_configuration_never_queries_database(db, monkeypatch, base):
    monkeypatch.setattr(settings, "frontend_url", base)
    monkeypatch.setattr(db, "execute", lambda *_args, **_kwargs: pytest.fail("bad config reached DB"))
    with pytest.raises(mail.RegistrationMailUnavailable):
        mail.prepare(db, "recipient@example.com", "en")


@pytest.mark.parametrize("locale", ["en", "ru", "cz", "unsupported"])
def test_committed_capture_has_fragment_and_no_plain_secret_in_database_repr_logs_or_file(db, monkeypatch, tmp_path, caplog, locale):
    file = tmp_path / "global-capture.jsonl"
    monkeypatch.setattr(settings, "capture_mail_file", str(file))
    tables = (User, Client, Business, Subscription, NotificationOutbox, VisitorAccount, VisitorSession)
    before = [db.query(table).count() for table in tables]
    prepared = committed_capture(db, "  Recipient+Tag@EXAMPLE.COM  ", locale)
    assert len(mail.captured_messages()) == 1
    raw = token(prepared)
    assert raw.startswith("vch_") and len(raw) == 47
    assert "https://visitor.example/visitor/register#token=" + raw in prepared.text
    assert prepared.recipient == "recipient+tag@example.com"
    assert raw not in repr(prepared) and prepared.recipient not in repr(prepared)
    assert not file.exists() and raw not in caplog.text
    row = db.query(VisitorChallenge).one()
    assert row.token_hash == hashlib.sha256(raw.encode()).hexdigest()
    assert row.purpose == "register" and row.email_canonical == prepared.recipient
    assert row.expires_at - row.created_at == timedelta(minutes=30)
    assert raw not in [getattr(row, column.name) for column in row.__table__.columns]
    assert [db.query(table).count() for table in tables] == before


@pytest.mark.parametrize("kind", ["unknown", "password", "google", "crm"])
def test_all_email_identities_follow_the_same_request_queries_without_account_lookup(db, make_user, kind):
    email = "recipient@example.com"
    if kind in {"password", "google"}:
        row = VisitorAccount(email_canonical=email, email_verified_at=NOW,
            hashed_password="existing-password" if kind == "password" else None)
        db.add(row)
        db.flush()
        if kind == "google":
            db.add(VisitorGoogleIdentity(subject="known-subject", account_id=row.id))
        db.commit()
    elif kind == "crm":
        make_user(email=email)
    statements = []
    def record(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.lower())
    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        message = committed_capture(db, email)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert message.recipient == email and len(mail.captured_messages()) == 1
    assert not any("visitor_accounts" in sql or "users" in sql or "clients" in sql for sql in statements)


def test_cooldown_and_hour_cap_are_database_based_and_do_not_invalidate_previous_proofs(db, capture_mode):
    issued = []
    for _ in range(5):
        prepared = committed_capture(db)
        assert prepared is not None
        issued.append(prepared)
        db.rollback()
        assert committed_capture(db) is None
        capture_mode[0] += timedelta(seconds=61)
    assert committed_capture(db) is None
    assert db.query(VisitorChallenge).count() == 5
    assert all(row.consumed_at is None for row in db.query(VisitorChallenge))
    assert len(mail.captured_messages()) == 5
    capture_mode[0] = NOW + timedelta(hours=1, seconds=1)
    assert committed_capture(db) is not None
    assert db.query(VisitorChallenge).count() == 6


def test_same_email_race_creates_one_proof_and_one_capture(db):
    barrier = Barrier(2)
    def request(_):
        with Session(db.get_bind()) as transaction:
            barrier.wait(timeout=5)
            message = mail.prepare(transaction, "recipient@example.com", "en")
            transaction.commit()
            if message is not None:
                mail.capture_after_commit(transaction, message)
            return message is not None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(request, range(2)))
    assert sorted(results) == [False, True]
    assert db.query(VisitorChallenge).count() == len(mail.captured_messages()) == 1


def test_capture_before_commit_after_rollback_and_after_other_read_are_refused(db):
    message = mail.prepare(db, "recipient@example.com", "en")
    with pytest.raises(mail.RegistrationMailUnavailable):
        mail.capture_after_commit(db, message)
    db.rollback()
    with pytest.raises(mail.RegistrationMailUnavailable):
        mail.capture_after_commit(db, message)
    assert mail.captured_messages() == ()
    message = mail.prepare(db, "recipient@example.com", "en")
    db.commit()
    db.query(VisitorChallenge).count()
    with pytest.raises(mail.RegistrationMailUnavailable):
        mail.capture_after_commit(db, message)
    db.rollback()
    mail.capture_after_commit(db, message)
    mail.capture_after_commit(db, message)
    assert len(mail.captured_messages()) == 1


def test_real_deferred_commit_failure_never_emits_or_persists_a_proof(db):
    db.execute(text("""CREATE FUNCTION mail_capture_commit_failure() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'synthetic capture commit failure'; RETURN NEW; END $$"""))
    db.execute(text("""CREATE CONSTRAINT TRIGGER mail_capture_commit_failure
        AFTER INSERT ON visitor_challenges DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION mail_capture_commit_failure()"""))
    db.commit()
    try:
        message = mail.prepare(db, "recipient@example.com", "en")
        with pytest.raises(DBAPIError):
            db.commit()
        db.rollback()
        with pytest.raises(mail.RegistrationMailUnavailable):
            mail.capture_after_commit(db, message)
        assert mail.captured_messages() == ()
        assert db.query(VisitorChallenge).count() == 0
    finally:
        db.rollback()
        db.execute(text("DROP TRIGGER mail_capture_commit_failure ON visitor_challenges"))
        db.execute(text("DROP FUNCTION mail_capture_commit_failure()"))
        db.commit()


def test_global_live_gmail_flags_do_not_activate_visitor_delivery(db, monkeypatch):
    monkeypatch.setattr(settings, "notification_transport", "live")
    monkeypatch.setattr(settings, "email_provider", "gmail")
    monkeypatch.setattr(settings, "gmail_delivery_enabled", True)
    monkeypatch.setattr(settings, "pilot_email_delivery_enabled", True)
    committed_capture(db)
    assert len(mail.captured_messages()) == 1
    monkeypatch.setattr(settings, "visitor_auth_mail_mode", "disabled")
    with pytest.raises(mail.RegistrationMailUnavailable):
        mail.prepare(db, "other@example.com", "en")


def test_capture_is_bounded_without_files_or_background_workers(db):
    for number in range(66):
        committed_capture(db, f"recipient{number}@example.com")
    messages = mail.captured_messages()
    assert len(messages) == 64
    assert messages[0].recipient == "recipient2@example.com"
    assert messages[-1].recipient == "recipient65@example.com"


@pytest.mark.parametrize("state", ["expired", "consumed"])
def test_capture_requires_still_live_unused_committed_proof(db, capture_mode, state):
    message = mail.prepare(db, "recipient@example.com", "en")
    db.commit()
    if state == "expired":
        capture_mode[0] = NOW + timedelta(minutes=30)
    else:
        db.query(VisitorChallenge).filter_by(id=message.challenge_id).update({"consumed_at": NOW})
        db.commit()
    with pytest.raises(mail.RegistrationMailUnavailable):
        mail.capture_after_commit(db, message)
    assert mail.captured_messages() == ()


def test_cooldown_clock_is_read_after_real_advisory_lock_wait(db, capture_mode):
    engine = db.get_bind()
    observed = {}
    def first_key(_conn, _cursor, statement, parameters, _context, _many):
        if "pg_advisory_xact_lock" in statement:
            observed["key"] = parameters["key"]
    event.listen(engine, "before_cursor_execute", first_key)
    try:
        committed_capture(db)
    finally:
        event.remove(engine, "before_cursor_execute", first_key)
    capture_mode[0] = NOW + timedelta(seconds=59)
    entering = Event()
    def waiter_query(conn, _cursor, statement, _parameters, _context, _many):
        if "pg_advisory_xact_lock" in statement:
            observed["waiter_pid"] = conn.connection.driver_connection.get_backend_pid()
            entering.set()
    def request():
        with Session(engine) as transaction:
            return committed_capture(transaction)
    with Session(engine) as holder:
        holder.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": observed["key"]})
        holder_pid = holder.execute(text("SELECT pg_backend_pid()")).scalar_one()
        event.listen(engine, "before_cursor_execute", waiter_query)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(request)
                try:
                    assert entering.wait(timeout=5)
                    deadline = time.monotonic() + 5
                    with engine.connect() as observer:
                        while time.monotonic() < deadline:
                            blockers = observer.execute(text("SELECT pg_blocking_pids(:pid)"),
                                {"pid": observed["waiter_pid"]}).scalar_one()
                            if holder_pid in blockers:
                                break
                            time.sleep(0.01)
                        else:
                            pytest.fail("PostgreSQL did not confirm advisory-lock wait")
                    capture_mode[0] = NOW + timedelta(seconds=60)
                finally:
                    holder.rollback()
                assert future.result(timeout=10) is not None
        finally:
            event.remove(engine, "before_cursor_execute", waiter_query)
    assert db.query(VisitorChallenge).count() == len(mail.captured_messages()) == 2
