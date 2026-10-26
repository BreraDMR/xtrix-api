"""Reset requests never enable real delivery or accept a new password early."""
from urllib.parse import parse_qs, urlsplit

import pytest

from app.api.core.config import settings
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.user import User
from app.db.models.visitor_account import VisitorAccount
from app.db.models.visitor_auth import VisitorChallenge, VisitorSession

URL = "/visitor/auth/password/reset/request"


def private(response, status):
    assert response.status_code == status, response.text
    assert response.headers.get("cache-control") == "no-store"


def test_request_is_disabled_by_default_without_creating_proof(client, db):
    response = client.post(URL, json={"email": "request@example.com"})
    private(response, 503)
    assert settings.visitor_auth_mail_mode == "disabled"
    assert db.query(VisitorChallenge).count() == db.query(VisitorAccount).count() == 0


def test_request_cannot_preset_a_password_before_email_proof(client, db):
    password = "private-attacker-password-canary"
    response = client.post(URL, json={"email": "request@example.com", "password": password})
    private(response, 422)
    assert password not in response.text
    assert db.query(VisitorChallenge).count() == db.query(VisitorAccount).count() == 0


def test_invalid_email_is_rejected_without_delivery_or_proof(client, db):
    private(client.post(URL, json={"email": "not-an-email"}), 422)
    assert db.query(VisitorChallenge).count() == 0


@pytest.fixture
def capture(monkeypatch):
    from app.api.core import visitor_reset_mail

    monkeypatch.setattr(settings, "visitor_auth_mail_mode", "capture")
    monkeypatch.setattr(settings, "environment", "test")
    monkeypatch.setattr(settings, "frontend_url", "https://visitor.example")
    visitor_reset_mail.clear_capture()
    yield visitor_reset_mail
    visitor_reset_mail.clear_capture()


def captured_token(message):
    link = next(line for line in message.text.splitlines() if line.startswith("https://"))
    parsed = urlsplit(link)
    assert parsed.scheme == "https" and parsed.netloc == "visitor.example"
    assert parsed.path == "/visitor/reset-password" and not parsed.query
    return parse_qs(parsed.fragment)["token"][0]


def test_capture_request_then_reset_never_sends_or_changes_crm(client, db, capture):
    from app.api.core.clock import utcnow
    from app.api.core import visitor_passwords
    account = VisitorAccount(email_canonical="new.visitor+tag@example.com", email_verified_at=utcnow(),
                             hashed_password=visitor_passwords.hash_password("Old!Visitor-57-Pass"))
    db.add(account)
    db.commit()
    before = [db.query(table).count() for table in (User, NotificationOutbox)]
    response = client.post(URL, json={"email": " New.Visitor+tag@EXAMPLE.COM "},
                           headers={"Accept-Language": "ru", "Host": "attacker.example"})
    private(response, 202)
    assert set(response.json()) == {"message"}
    assert db.query(VisitorAccount).count() == 1 and db.query(VisitorSession).count() == 0
    message, = capture.captured_messages()
    token = captured_token(message)
    assert token not in response.text and message.recipient not in response.text
    proof = db.query(VisitorChallenge).one()
    assert proof.purpose == "reset" and proof.consumed_at is None
    assert visitor_passwords.verify_password("Old!Visitor-57-Pass", account.hashed_password)
    completed = client.post("/visitor/auth/password/reset/confirm",
        json={"token": token, "new_password": "Safe!Visitor-57-Pass"})
    private(completed, 204)
    assert not completed.content and db.query(VisitorSession).count() == 0
    db.expire_all()
    assert visitor_passwords.verify_password("Safe!Visitor-57-Pass", account.hashed_password)
    assert [db.query(table).count() for table in (User, NotificationOutbox)] == before


def test_existing_email_and_cooldown_have_identical_public_responses(client, db, capture):
    from app.api.core.clock import utcnow

    db.add(VisitorAccount(email_canonical="existing@example.com", email_verified_at=utcnow()))
    db.commit()
    responses = [client.post(URL, json={"email": email}) for email in [
        "new@example.com", "existing@example.com", "new@example.com", "existing@example.com"]]
    for response in responses:
        private(response, 202)
    assert all(response.json() == responses[0].json() for response in responses)
    assert len(capture.captured_messages()) == db.query(VisitorChallenge).count() == 1
    assert db.query(VisitorAccount).count() == 1


@pytest.mark.parametrize("environment", ["production", "staging"])
def test_capture_is_unavailable_on_deployed_environments(client, db, capture, monkeypatch, environment):
    monkeypatch.setattr(settings, "environment", environment)
    private(client.post(URL, json={"email": "request@example.com"}), 503)
    assert not capture.captured_messages()
    assert db.query(VisitorChallenge).count() == 0


def test_failed_commit_does_not_capture_a_rolled_back_link(client, db, capture, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session
    from app.api.deps import get_db
    from app.main import app

    def broken_db():
        with Session(db.get_bind()) as transaction:
            def fail():
                raise RuntimeError("synthetic reset request commit failure")
            monkeypatch.setattr(transaction, "commit", fail)
            yield transaction
    from app.api.core.clock import utcnow
    db.add(VisitorAccount(email_canonical="request@example.com", email_verified_at=utcnow()))
    db.commit()
    app.dependency_overrides[get_db] = broken_db
    try:
        with TestClient(app, raise_server_exceptions=False) as failing_client:
            response = failing_client.post(URL, json={"email": "request@example.com"})
        assert response.status_code == 500
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert not capture.captured_messages()
    assert db.query(VisitorChallenge).count() == 0


def test_capture_failure_after_commit_keeps_proof_but_reports_no_delivery(client, db, capture, monkeypatch):
    from app.api.core.clock import utcnow

    db.add(VisitorAccount(email_canonical="existing@example.com", email_verified_at=utcnow()))
    db.commit()
    original = capture.capture_after_commit
    def unavailable(*_args):
        raise capture.ResetMailUnavailable("synthetic local capture failure")
    monkeypatch.setattr(capture, "capture_after_commit", unavailable)
    response = client.post(URL, json={"email": "existing@example.com"})
    private(response, 503)
    proof, = db.query(VisitorChallenge).all()
    assert proof.purpose == "reset" and proof.consumed_at is None
    assert not capture.captured_messages()
    monkeypatch.setattr(capture, "capture_after_commit", original)
    # The committed proof still counts towards cooldown; a retry is not a delivery retry.
    private(client.post(URL, json={"email": "existing@example.com"}), 202)
    assert db.query(VisitorChallenge).count() == 1
    assert not capture.captured_messages()


@pytest.mark.parametrize("locale", ["en", "ru", "cz"])
def test_unknown_email_response_is_localized_generic_and_private(client, db, capture, locale):
    from app.api.core.i18n import _

    response = client.post(URL, json={"email": "unknown@example.com"}, headers={"Accept-Language": locale})
    private(response, 202)
    assert response.json() == {"message": _("visitor_password_reset_requested", locale)}
    assert "unknown@example.com" not in response.text
    assert not capture.captured_messages() and db.query(VisitorChallenge).count() == 0
