import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from google.auth.exceptions import GoogleAuthError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.api.core.clock import utcnow
from app.api.core.config import settings

ORIGIN = "https://pilot.example.com"


@pytest.fixture
def google_setup(monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "pilot.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "frontend_url", ORIGIN)
    monkeypatch.setattr(settings, "pilot_mode_enabled", True)
    monkeypatch.setattr(settings, "pilot_invited_emails", "invited@example.com")


def challenge(client):
    response = client.post("/auth/google/nonce", headers={"Origin": ORIGIN})
    assert response.status_code == 200, response.text
    return response.json()["nonce"]


def sign_in(client, monkeypatch, *, email="invited@example.com", sub="google-123", **extra):
    from app.api.core import google_identity

    nonce = challenge(client)
    monkeypatch.setattr(google_identity, "verify_credential", lambda token: {
        "sub": sub, "email": email, "email_verified": True,
        "name": "Invited Owner", "nonce": nonce,
    })
    return client.post("/auth/google/login", headers={"Origin": ORIGIN}, json={
        "credential": "synthetic-google-token", "nonce": nonce, **extra,
    })


def test_google_login_is_disabled_without_client_id(client, monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "")
    assert client.get("/auth/google/config").json() == {"enabled": False, "client_id": None}
    assert client.post("/auth/google/nonce", headers={"Origin": ORIGIN}).status_code == 503


def test_google_nonce_rejects_cross_site_origin(client, google_setup):
    assert client.post("/auth/google/nonce", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/auth/google/nonce").status_code == 403


def test_new_google_login_creates_one_trial_and_reuses_identity(client, db, monkeypatch, google_setup):
    from app.db.models.google_identity import GoogleIdentity
    from app.db.models.subscription import Subscription
    from app.db.models.user import User

    response = sign_in(client, monkeypatch)
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    me = client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["role"] == "business_owner"
    user = db.query(User).filter_by(email="invited@example.com").one()
    assert db.query(GoogleIdentity).filter_by(user_id=user.id).count() == 1
    assert db.query(Subscription).filter_by(user_id=user.id).count() == 1
    trial = db.query(Subscription).filter_by(user_id=user.id).one()
    original_end = trial.trial_end
    assert timedelta(days=6) < trial.trial_end - utcnow() <= timedelta(days=7)
    assert sign_in(client, monkeypatch).status_code == 200
    db.expire_all()
    assert db.query(Subscription).filter_by(user_id=user.id).one().trial_end == original_end


def test_google_signup_requires_invitation(client, db, monkeypatch, google_setup):
    from app.db.models.user import User

    assert sign_in(client, monkeypatch, email="other@example.com").status_code == 403
    assert db.query(User).filter_by(email="other@example.com").count() == 0


def test_existing_email_is_never_linked_without_password_and_explicit_consent(client, db, make_user, monkeypatch, google_setup):
    from app.db.models.google_identity import GoogleIdentity

    user = make_user(email="invited@example.com")
    assert sign_in(client, monkeypatch).status_code == 409
    assert db.query(GoogleIdentity).count() == 0
    assert sign_in(client, monkeypatch, link_existing=True, password="Wrong-password!").status_code == 401
    response = sign_in(client, monkeypatch, link_existing=True, password="Str0ng!Pass-word")
    assert response.status_code == 200, response.text
    assert db.query(GoogleIdentity).one().user_id == user.id


def test_google_credential_is_nonce_bound_and_single_use(client, monkeypatch, google_setup):
    from app.api.core import google_identity

    nonce = challenge(client)
    claims = {"sub": "google-123", "email": "invited@example.com", "email_verified": True, "nonce": "wrong"}
    monkeypatch.setattr(google_identity, "verify_credential", lambda token: claims)
    body = {"nonce": nonce, "credential": "synthetic-token"}
    assert client.post("/auth/google/login", headers={"Origin": ORIGIN}, json=body).status_code == 401
    claims["nonce"] = nonce
    assert client.post("/auth/google/login", headers={"Origin": ORIGIN}, json=body).status_code == 200
    assert client.post("/auth/google/login", headers={"Origin": ORIGIN}, json=body).status_code == 401


@pytest.mark.parametrize("claims_patch", [{"email_verified": False}, {"email_verified": "true"}, {"sub": ""}])
def test_google_claims_must_be_verified(client, monkeypatch, google_setup, claims_patch):
    from app.api.core import google_identity

    nonce = challenge(client)
    claims = {"sub": "google-123", "email": "invited@example.com", "email_verified": True, "nonce": nonce, **claims_patch}
    monkeypatch.setattr(google_identity, "verify_credential", lambda token: claims)
    response = client.post("/auth/google/login", headers={"Origin": ORIGIN}, json={"nonce": nonce, "credential": "synthetic"})
    assert response.status_code == 401


def test_google_login_does_not_bypass_two_factor_or_deleted_account(client, db, make_user, monkeypatch, google_setup):
    import pyotp

    user = make_user(email="invited@example.com", role="business_owner")
    user.is_2fa_enabled = True
    user.totp_secret = pyotp.random_base32()
    db.commit()
    response = sign_in(client, monkeypatch, link_existing=True, password="Str0ng!Pass-word")
    assert response.status_code == 200 and response.json()["requires_2fa"] is True
    response = sign_in(client, monkeypatch, link_existing=True, password="Str0ng!Pass-word", totp_token=pyotp.TOTP(user.totp_secret).now())
    assert response.status_code == 200 and response.json()["access_token"]
    user.deleted_at = utcnow()
    db.commit()
    assert sign_in(client, monkeypatch).status_code == 401


def test_real_google_sdk_checks_signature_audience_issuer_and_expiry(monkeypatch, google_setup):
    from app.api.core import google_identity

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    monkeypatch.setattr(google_identity, "_google_request", lambda *a, **kw: SimpleNamespace(status=200, data=json.dumps({"test-key": public}).encode()))
    now = int(datetime.now(timezone.utc).timestamp())
    claims = {"iss": "https://accounts.google.com", "sub": "123", "aud": settings.google_client_id, "iat": now, "exp": now + 300}
    def encoded(payload, key=private):
        return jwt.encode(payload, key, algorithm="RS256", headers={"kid": "test-key"})
    assert google_identity.verify_credential(encoded(claims))["sub"] == "123"
    for bad in ({"aud": "another-app"}, {"iss": "https://evil.example"}, {"exp": now - 300}):
        with pytest.raises((ValueError, GoogleAuthError)):
            google_identity.verify_credential(encoded({**claims, **bad}))
    with pytest.raises((ValueError, GoogleAuthError)):
        google_identity.verify_credential(encoded(claims, rsa.generate_private_key(public_exponent=65537, key_size=2048)))


def test_expired_google_nonce_and_locked_user_are_rejected(client, db, make_user, monkeypatch, google_setup):
    from app.api.core import google_identity
    from app.db.models.google_identity import GoogleLoginNonce

    nonce = challenge(client)
    challenge_row = db.query(GoogleLoginNonce).one()
    challenge_row.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    monkeypatch.setattr(google_identity, "verify_credential", lambda token: {
        "sub": "google-123", "email": "invited@example.com", "email_verified": True, "nonce": nonce,
    })
    assert client.post("/auth/google/login", headers={"Origin": ORIGIN}, json={"nonce": nonce, "credential": "synthetic"}).status_code == 401
    user = make_user(email="invited@example.com")
    user.is_locked = True
    user.locked_until = None
    db.commit()
    response = sign_in(client, monkeypatch, link_existing=True, password="Str0ng!Pass-word")
    assert response.status_code == 423


def test_google_login_never_accepts_a_role_escalation(client, google_setup):
    response = client.post("/auth/google/login", headers={"Origin": ORIGIN}, json={
        "nonce": "x" * 43, "credential": "synthetic", "role": "admin",
    })
    assert response.status_code == 422


def test_google_credentials_are_redacted_in_detailed_errors(client, monkeypatch, google_setup):
    monkeypatch.setattr(settings, "detailed_errors", True)
    credential = "do-not-echo-google-credential"
    response = client.post("/auth/google/login", headers={"Origin": ORIGIN}, json={
        "nonce": "do-not-echo-this-nonce", "credential": credential, "role": "admin",
    })
    assert response.status_code == 422
    assert credential not in response.text
    assert "do-not-echo-this-nonce" not in response.text


def test_google_nonce_only_works_from_the_origin_that_requested_it(client, monkeypatch, google_setup):
    from app.api.core import google_identity

    monkeypatch.setattr(settings, "cors_origins", f"{ORIGIN},https://second.example.com")
    nonce = challenge(client)
    monkeypatch.setattr(google_identity, "verify_credential", lambda token: {
        "sub": "google-123", "email": "invited@example.com", "email_verified": True, "nonce": nonce,
    })
    body = {"nonce": nonce, "credential": "synthetic"}
    assert client.post("/auth/google/login", headers={"Origin": "https://evil.example"}, json=body).status_code == 403
    response = client.post("/auth/google/login", headers={"Origin": "https://second.example.com"}, json=body)
    assert response.status_code == 401 and "google_nonce_invalid" in response.text
    assert client.post("/auth/google/login", headers={"Origin": ORIGIN}, json=body).status_code == 200


def test_google_only_account_has_no_usable_password(client, monkeypatch, google_setup):
    assert sign_in(client, monkeypatch).status_code == 200
    for password in ("", "Str0ng!Pass-word"):
        response = client.post("/users/login", json={"email": "invited@example.com", "password": password})
        assert response.status_code in (401, 422), response.text
        assert "access_token" not in response.text


def test_expired_google_trial_is_not_renewed_by_signing_in_again(client, db, monkeypatch, google_setup):
    from app.db.models.subscription import Subscription
    from app.db.models.user import User

    assert sign_in(client, monkeypatch).status_code == 200
    user = db.query(User).filter_by(email="invited@example.com").one()
    trial = db.query(Subscription).filter_by(user_id=user.id).one()
    trial.trial_end = utcnow() - timedelta(days=4)
    trial.current_period_end = trial.trial_end
    db.commit()
    again = sign_in(client, monkeypatch)
    assert again.status_code == 200
    headers = {"Authorization": f"Bearer {again.json()['access_token']}"}
    assert client.get("/subscriptions/access", headers=headers).json()["crm_mode"] == "blocked"
    # Another Google account claiming the same address must not start a second trial either.
    assert sign_in(client, monkeypatch, sub="google-other").status_code == 409
    db.expire_all()
    assert db.query(Subscription).filter_by(user_id=user.id).count() == 1
    assert db.query(User).filter_by(email="invited@example.com").count() == 1
