"""Registration proof creates a new visitor, never adopts an existing account."""
import pytest

from app.api.core import visitor_challenges, visitor_sessions
from app.api.core.clock import utcnow
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.subscription import Subscription
from app.db.models.user import User
from app.db.models.visitor_account import VisitorAccount, VisitorGoogleIdentity
from app.db.models.visitor_auth import VisitorChallenge, VisitorSession

URL = "/visitor/auth/register/complete"
PASSWORD = "Safe!Visitor-57-Pass"


def issue(db, email="new.visitor@example.com"):
    proof = visitor_challenges.issue_challenge(db, "register", email=email)
    raw, row_id = proof.raw_token, proof.row.id
    db.commit()
    return raw, row_id


def private(response, status):
    assert response.status_code == status, response.text
    assert response.headers.get("cache-control") == "no-store"
    assert "set-cookie" not in response.headers


def test_completion_creates_only_visitor_state_and_issues_session_after_proof(client, db):
    token, proof_id = issue(db)
    tables = (User, Client, Business, Subscription, NotificationOutbox)
    before = [db.query(table).count() for table in tables]
    response = client.post(URL, json={"token": token, "new_password": PASSWORD})
    private(response, 201)
    data = response.json()
    assert set(data) == {"session_token", "expires_at"}
    assert data["expires_at"].endswith("Z") and data["session_token"].startswith("vst_")
    session = visitor_sessions.get_valid_session(db, data["session_token"])
    assert session is not None
    profile = client.get("/visitor/me", headers={"X-Visitor-Token": data["session_token"]})
    private(profile, 200)
    assert profile.json()["email"] == "new.visitor@example.com"
    assert profile.json()["has_password"] is True
    assert profile.json()["google_connected"] is False
    assert db.get(VisitorChallenge, proof_id).consumed_at is not None
    assert [db.query(table).count() for table in tables] == before
    replay = client.post(URL, json={"token": token, "new_password": PASSWORD})
    private(replay, 400)
    assert data["session_token"] not in replay.text and token not in replay.text
    assert db.query(VisitorAccount).count() == db.query(VisitorSession).count() == 1


def test_weak_password_error_rolls_back_consumption_so_recipient_can_retry(client, db):
    token, proof_id = issue(db)
    response = client.post(URL, json={"token": token, "new_password": "short"})
    private(response, 422)
    assert db.get(VisitorChallenge, proof_id).consumed_at is None
    assert db.query(VisitorAccount).count() == db.query(VisitorSession).count() == 0
    private(client.post(URL, json={"token": token, "new_password": PASSWORD}), 201)


def test_existing_google_account_is_not_adopted_or_given_a_password(client, db):
    account = VisitorAccount(email_canonical="new.visitor@example.com", email_verified_at=utcnow())
    db.add(account)
    db.flush()
    db.add(VisitorGoogleIdentity(subject="existing-google-subject", account_id=account.id))
    db.commit()
    account_id = account.id
    token, proof_id = issue(db)
    response = client.post(URL, json={"token": token, "new_password": PASSWORD})
    private(response, 409)
    db.expire_all()
    assert db.get(VisitorAccount, account_id).hashed_password is None
    assert db.get(VisitorChallenge, proof_id).consumed_at is None
    assert db.query(VisitorAccount).count() == db.query(VisitorGoogleIdentity).count() == 1
    assert db.query(VisitorSession).count() == 0


@pytest.mark.parametrize("body", [
    {"token": "synthetic-private-proof", "new_password": PASSWORD, "email": "other@example.com"},
    {"token": "synthetic-private-proof", "new_password": PASSWORD, "account_id": 1},
    {"token": "synthetic-private-proof", "new_password": True},
])
def test_request_cannot_choose_identity_and_validation_does_not_echo_credentials(client, monkeypatch, body):
    from app.api.core.config import settings
    monkeypatch.setattr(settings, "detailed_errors", True)
    response = client.post(URL, json=body)
    private(response, 422)
    assert PASSWORD not in response.text and "synthetic-private-proof" not in response.text


@pytest.mark.parametrize("locale", ["en", "ru", "cz"])
def test_invalid_proof_is_generic_localized_and_does_not_create_account(client, db, locale):
    from app.api.core.i18n import _
    response = client.post(URL, json={"token": "synthetic-private-proof", "new_password": PASSWORD},
                           headers={"Accept-Language": locale})
    private(response, 400)
    assert response.json()["error"]["message"] == _("visitor_registration_invalid", locale)
    assert db.query(VisitorAccount).count() == db.query(VisitorSession).count() == 0
