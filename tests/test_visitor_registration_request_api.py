"""Registration requests never enable real delivery or accept a preset password."""
from urllib.parse import parse_qs, urlsplit

import pytest

from app.api.core.config import settings
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.user import User
from app.db.models.visitor_account import VisitorAccount
from app.db.models.visitor_auth import VisitorChallenge, VisitorSession

URL = "/visitor/auth/register/request"


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
    from app.api.core import visitor_registration_mail

    monkeypatch.setattr(settings, "visitor_auth_mail_mode", "capture")
    monkeypatch.setattr(settings, "environment", "test")
    monkeypatch.setattr(settings, "frontend_url", "https://visitor.example")
    visitor_registration_mail.clear_capture()
    yield visitor_registration_mail
    visitor_registration_mail.clear_capture()


def captured_token(message):
    link = next(line for line in message.text.splitlines() if line.startswith("https://"))
    parsed = urlsplit(link)
    assert parsed.scheme == "https" and parsed.netloc == "visitor.example"
    assert parsed.path == "/visitor/register" and not parsed.query
    return parse_qs(parsed.fragment)["token"][0]


def test_capture_request_then_proof_completion_never_sends_or_creates_crm(client, db, capture):
    before = [db.query(table).count() for table in (User, NotificationOutbox)]
    response = client.post(URL, json={"email": " New.Visitor+tag@EXAMPLE.COM "},
                           headers={"Accept-Language": "ru", "Host": "attacker.example"})
    private(response, 202)
    assert set(response.json()) == {"message"}
    assert db.query(VisitorAccount).count() == db.query(VisitorSession).count() == 0
    message, = capture.captured_messages()
    assert message.recipient == "new.visitor+tag@example.com"
    token = captured_token(message)
    assert token not in response.text and message.recipient not in response.text
    assert db.query(VisitorChallenge).one().consumed_at is None
    completed = client.post("/visitor/auth/register/complete",
        json={"token": token, "new_password": "Safe!Visitor-57-Pass"})
    private(completed, 201)
    assert completed.json()["session_token"].startswith("vst_")
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
    assert len(capture.captured_messages()) == db.query(VisitorChallenge).count() == 2
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
                raise RuntimeError("synthetic registration request commit failure")
            monkeypatch.setattr(transaction, "commit", fail)
            yield transaction
    app.dependency_overrides[get_db] = broken_db
    try:
        with TestClient(app, raise_server_exceptions=False) as failing_client:
            response = failing_client.post(URL, json={"email": "request@example.com"})
        assert response.status_code == 500
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert not capture.captured_messages()
    assert db.query(VisitorChallenge).count() == 0
