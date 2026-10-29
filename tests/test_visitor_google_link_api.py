"""Linking needs a recent visitor session and its own signed Google challenge."""
from datetime import timedelta

import pytest

from app.api.core import visitor_sessions
from app.api.core.clock import utcnow
from app.api.core.config import settings
from app.db.models.visitor_account import VisitorAccount, VisitorGoogleIdentity
from app.db.models.visitor_auth import VisitorChallenge, VisitorSession
from tests import test_visitor_google_credentials as google_fixtures

signed = google_fixtures.signed
BASE = "/visitor/auth/google"
ORIGIN = "https://visitor.example"


@pytest.fixture
def actor(db, monkeypatch):
    monkeypatch.setattr(settings, "frontend_url", ORIGIN)
    account = VisitorAccount(email_canonical="actor@example.com", email_verified_at=utcnow(),
                             hashed_password="unchanged-password-canary")
    db.add(account)
    db.flush()
    session, raw = visitor_sessions.create_session(db, account)
    result = account.id, session.id, raw
    db.commit()
    return result


def private(response, status):
    assert response.status_code == status, response.text
    assert response.headers.get("cache-control") == "no-store"


def issue(client, actor):
    headers = {"Origin": ORIGIN, "X-Visitor-Token": actor[2]}
    response = client.post(BASE + "/link/nonce", json={}, headers=headers)
    private(response, 200)
    assert response.json()["expires_in"] == 300
    return headers, response.json()["nonce"]


def test_explicit_link_keeps_email_password_and_sessions_and_enables_google_login(client, db, actor, signed):
    headers, raw = issue(client, actor)
    proof = db.query(VisitorChallenge).one()
    assert proof.purpose == "google_link" and proof.account_id == actor[0]
    body = {"nonce": raw, "credential": signed.encode({"nonce": raw})}
    linked = client.post(BASE + "/link", json=body, headers=headers)
    private(linked, 204)
    assert not linked.content
    account = db.get(VisitorAccount, actor[0])
    assert account.email_canonical == "actor@example.com"
    assert account.hashed_password == "unchanged-password-canary"
    assert db.query(VisitorSession).count() == db.query(VisitorGoogleIdentity).count() == 1
    private(client.post(BASE + "/link", json=body, headers=headers), 401)
    response = client.post(BASE + "/nonce", json={}, headers={"Origin": ORIGIN})
    private(response, 200)
    login_nonce = response.json()["nonce"]
    logged_in = client.post(BASE + "/login", headers={"Origin": ORIGIN}, json={
        "nonce": login_nonce, "credential": signed.encode({"nonce": login_nonce})})
    private(logged_in, 200)
    resolved = visitor_sessions.get_valid_session(db, logged_in.json()["session_token"])
    assert resolved.account_id == actor[0]
    assert db.query(VisitorAccount).count() == 1


def test_old_session_can_read_profile_but_cannot_issue_link_nonce(client, db, actor, signed):
    db.get(VisitorSession, actor[1]).created_at = utcnow() - timedelta(minutes=6)
    db.commit()
    headers = {"Origin": ORIGIN, "X-Visitor-Token": actor[2]}
    private(client.get("/visitor/me", headers=headers), 200)
    private(client.post(BASE + "/link/nonce", json={}, headers=headers), 403)
    assert db.query(VisitorChallenge).count() == 0


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer synthetic-crm-token"},
    {"X-Portal-Token": "synthetic-legacy-token"}])
def test_link_does_not_accept_crm_or_legacy_credentials(client, db, actor, signed, headers):
    response = client.post(BASE + "/link", headers={"Origin": ORIGIN, **headers}, json={
        "nonce": signed.nonce, "credential": signed.encode()})
    private(response, 401)
    assert not signed.calls
    assert db.query(VisitorGoogleIdentity).count() == 0


def test_link_does_not_transfer_another_accounts_google_identity(client, db, actor, signed):
    other = VisitorAccount(email_canonical="other@example.com", email_verified_at=utcnow())
    db.add(other)
    db.flush()
    db.add(VisitorGoogleIdentity(subject=signed.claims["sub"], account_id=other.id))
    other_id = other.id
    db.commit()
    headers, raw = issue(client, actor)
    response = client.post(BASE + "/link", headers=headers, json={
        "nonce": raw, "credential": signed.encode({"nonce": raw})})
    private(response, 409)
    assert db.query(VisitorGoogleIdentity).one().account_id == other_id
    assert db.query(VisitorChallenge).one().consumed_at is None
