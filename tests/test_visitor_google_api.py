"""Visitor Google HTTP uses signed local fixtures, never real Google OAuth."""
from datetime import timedelta

import pytest

from app.api.core import visitor_sessions
from app.api.core.clock import utcnow
from app.api.core.config import settings
from app.db.models.business import Business
from app.db.models.google_identity import GoogleIdentity, GoogleLoginNonce
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.subscription import Subscription
from app.db.models.user import User
from app.db.models.visitor_account import VisitorAccount, VisitorGoogleIdentity
from app.db.models.visitor_auth import VisitorChallenge, VisitorSession
from tests import test_visitor_google_credentials as google_fixtures

signed = google_fixtures.signed

BASE = "/visitor/auth/google"
ORIGIN = "https://visitor.example"


@pytest.fixture(autouse=True)
def visitor_origin(monkeypatch):
    monkeypatch.setattr(settings, "frontend_url", ORIGIN)


def private(response, status):
    assert response.status_code == status, response.text
    assert response.headers.get("cache-control") == "no-store"
    assert "set-cookie" not in response.headers


def nonce(client):
    response = client.post(BASE + "/nonce", json={}, headers={"Origin": ORIGIN})
    private(response, 200)
    data = response.json()
    assert set(data) == {"nonce", "expires_in"}
    assert data["nonce"].startswith("vch_") and data["expires_in"] == 300
    return data["nonce"]


def body(signed, raw_nonce):
    return {"nonce": raw_nonce, "credential": signed.encode({"nonce": raw_nonce})}


def test_config_is_separate_and_disabled_nonce_never_creates_a_proof(client, db, monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", " ")
    response = client.get(BASE + "/config")
    private(response, 200)
    assert response.json() == {"enabled": False, "client_id": None}
    private(client.post(BASE + "/nonce", json={}, headers={"Origin": ORIGIN}), 503)
    assert db.query(VisitorChallenge).count() == 0


def test_nonce_has_visitor_purpose_origin_and_only_hash_in_database(client, db, signed):
    raw = nonce(client)
    proof = db.query(VisitorChallenge).one()
    assert proof.purpose == "google_login" and proof.origin == ORIGIN
    assert proof.account_id is None and proof.email_canonical is None
    assert proof.expires_at - proof.created_at == timedelta(minutes=5)
    assert raw != proof.token_hash and raw not in repr(proof.__dict__)
    assert db.query(GoogleLoginNonce).count() == db.query(VisitorAccount).count() == 0


def test_missing_or_foreign_origin_is_forbidden_before_nonce_issue(client, db, signed):
    for origin in [None, "https://foreign.example", ORIGIN + "/", "https://visitor.example@foreign.example"]:
        headers = {"Origin": origin} if origin is not None else {}
        private(client.post(BASE + "/nonce", json={}, headers=headers), 403)
    assert db.query(VisitorChallenge).count() == 0


def test_signed_google_login_creates_only_visitor_and_replay_issues_no_session(client, db, signed):
    raw = nonce(client)
    counts = (User, Business, Subscription, NotificationOutbox, GoogleIdentity, GoogleLoginNonce)
    before = [db.query(table).count() for table in counts]
    request = body(signed, raw)
    response = client.post(BASE + "/login", json=request, headers={"Origin": ORIGIN})
    private(response, 200)
    data = response.json()
    assert set(data) == {"session_token", "expires_at"}
    assert data["expires_at"].endswith("Z")
    row = visitor_sessions.get_valid_session(db, data["session_token"])
    assert row is not None and row.expires_at - row.created_at == timedelta(days=30)
    profile = client.get("/visitor/me", headers={"X-Visitor-Token": data["session_token"]})
    private(profile, 200)
    assert profile.json()["google_connected"] is True and profile.json()["has_password"] is False
    assert profile.json()["email"] == "visitor+own@example.com"
    assert request["credential"] not in response.text and raw not in response.text
    assert [db.query(table).count() for table in counts] == before
    replay = client.post(BASE + "/login", json=request, headers={"Origin": ORIGIN})
    private(replay, 401)
    assert db.query(VisitorSession).count() == 1


def test_existing_visitor_email_requires_link_without_password_or_identity_changes(client, db, signed):
    existing = VisitorAccount(email_canonical="visitor+own@example.com", email_verified_at=utcnow(),
                              hashed_password="existing-password-hash-canary")
    db.add(existing)
    db.commit()
    owner_id = existing.id
    raw = nonce(client)
    response = client.post(BASE + "/login", json=body(signed, raw), headers={"Origin": ORIGIN})
    private(response, 409)
    db.expire_all()
    assert db.get(VisitorAccount, owner_id).hashed_password == "existing-password-hash-canary"
    assert db.query(VisitorAccount).count() == 1
    assert db.query(VisitorGoogleIdentity).count() == db.query(VisitorSession).count() == 0
    assert db.query(VisitorChallenge).one().consumed_at is None


@pytest.mark.parametrize("extra", [{"role": "admin"}, {"link_existing": True}, {"password": "private-canary"}])
def test_staff_signup_or_link_fields_are_forbidden_without_echoing_credentials(client, db, signed, extra):
    raw = nonce(client)
    request = {**body(signed, raw), **extra}
    response = client.post(BASE + "/login", json=request, headers={"Origin": ORIGIN})
    private(response, 422)
    assert raw not in response.text and request["credential"] not in response.text
    assert "private-canary" not in response.text
    assert not signed.calls
    assert db.query(VisitorAccount).count() == db.query(VisitorSession).count() == 0
