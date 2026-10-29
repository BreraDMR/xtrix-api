"""Signed synthetic Google JWTs use the installed verifier, without external OAuth."""
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import hashlib
import json
import secrets
from types import SimpleNamespace

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from google.auth import exceptions as google_errors
import jwt
import pytest

from app.api.core import google_identity
from app.api.core.config import settings
from app.api.core import visitor_google_credentials as visitor_google
from app.db.models.google_identity import GoogleIdentity, GoogleLoginNonce
from app.db.models.subscription import Subscription
from app.db.models.user import User
from app.db.models.visitor_account import VisitorAccount, VisitorGoogleIdentity
from app.db.models.visitor_auth import VisitorChallenge, VisitorSession


@pytest.fixture
def signed(monkeypatch):
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    monkeypatch.setattr(visitor_google, "utcnow", lambda: now)
    monkeypatch.setattr(settings, "google_client_id", "visitor-fixture.apps.googleusercontent.com")
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private.public_key().public_bytes(serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    calls = []
    def certificates(url, **_kwargs):
        calls.append(url)
        return SimpleNamespace(status=200, data=json.dumps({"visitor-test-key": public}).encode())
    monkeypatch.setattr(google_identity, "_google_request", certificates)
    nonce = "vch_" + secrets.token_urlsafe(32)
    claims = {"iss": "https://accounts.google.com", "sub": "visitor-subject-canary", "aud": settings.google_client_id,
        "iat": epoch, "exp": epoch + 3600, "email": "Visitor+Own@EXAMPLE.COM",
        "email_verified": True, "nonce": nonce, "name": "Untrusted Google Display Name", "role": "admin"}
    def encode(changes=None, key=None):
        return jwt.encode({**claims, **(changes or {})}, key or private,
                          algorithm="RS256", headers={"kid": "visitor-test-key"})
    return SimpleNamespace(now=now, epoch=epoch, claims=claims, nonce=nonce,
        encode=encode, calls=calls)


def test_installed_google_verifier_accepts_valid_signed_claims_and_returns_only_identity(signed):
    raw = signed.encode()
    claims = visitor_google.verify_credential(raw, signed.nonce)
    assert claims.issuer == "https://accounts.google.com"
    assert claims.subject == "visitor-subject-canary" and claims.email == "visitor+own@example.com"
    assert claims.issued_at == signed.now and claims.expires_at == signed.now + timedelta(hours=1)
    assert not hasattr(claims, "role") and not hasattr(claims, "name") and not hasattr(claims, "credential")
    assert raw not in repr(claims) and claims.email not in repr(claims) and claims.subject not in repr(claims)
    assert claims.nonce_hash == hashlib.sha256(signed.nonce.encode("ascii")).hexdigest()
    assert signed.nonce not in repr(claims) and claims.nonce_hash not in repr(claims)
    assert not hasattr(claims, "nonce")
    with pytest.raises(FrozenInstanceError):
        claims.email = "other@example.com"
    assert signed.calls == ["https://www.googleapis.com/oauth2/v1/certs"]


@pytest.mark.parametrize("issuer", ["https://accounts.google.com", "accounts.google.com"])
def test_both_google_issuer_spellings_have_one_database_identity(signed, issuer):
    claims = visitor_google.verify_credential(signed.encode({"iss": issuer}), signed.nonce)
    assert claims.issuer == "https://accounts.google.com"


@pytest.mark.parametrize("changes", [{"aud": "staff-or-other-app"}, {"iss": "https://attacker.example"},
    {"exp": 1}, {"iat": 4102444800}, {"email_verified": False}, {"email_verified": "true"},
    {"email_verified": 1}, {"email": "invalid"}, {"email": None}, {"sub": ""},
    {"sub": " visitor "}, {"sub": "x" * 256}, {"sub": None}, {"nonce": "wrong-nonce"},
    {"nonce": "\u043d\u043e\u043d\u0441"}, {"nonce": None}, {"exp": True}, {"exp": 1.5}])
def test_invalid_signed_claims_are_generic_and_never_return_an_identity(signed, changes):
    with pytest.raises(visitor_google.InvalidGoogleCredential, match="Invalid visitor Google credential"):
        visitor_google.verify_credential(signed.encode(changes), signed.nonce)


def test_invalid_signature_is_checked_by_the_real_installed_sdk(signed):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(visitor_google.InvalidGoogleCredential):
        visitor_google.verify_credential(signed.encode(key=other), signed.nonce)


def test_missing_timestamp_is_rejected_even_if_a_verifier_adapter_returns_claims(signed, monkeypatch):
    claims = dict(signed.claims)
    claims.pop("iat")
    monkeypatch.setattr(google_identity, "verify_credential", lambda _raw: claims)
    with pytest.raises(visitor_google.InvalidGoogleCredential):
        visitor_google.verify_credential("synthetic-adapter-token", signed.nonce)


def test_exact_expiry_is_invalid_and_freshness_must_be_rechecked_after_wait(signed, monkeypatch):
    claims = visitor_google.verify_credential(signed.encode(), signed.nonce)
    monkeypatch.setattr(visitor_google, "utcnow", lambda: claims.expires_at - timedelta(microseconds=1))
    visitor_google.require_fresh(claims)
    monkeypatch.setattr(visitor_google, "utcnow", lambda: claims.expires_at)
    with pytest.raises(visitor_google.InvalidGoogleCredential):
        visitor_google.require_fresh(claims)


@pytest.mark.parametrize("nonce", ["A" * 43, "vst_" + "A" * 43, "vch_" + "A" * 42 + "B",
    "vch_" + "A" * 43 + "\n", None, ""])
def test_expected_nonce_has_only_the_visitor_challenge_namespace(signed, nonce):
    with pytest.raises(visitor_google.InvalidGoogleCredential):
        visitor_google.verify_credential(signed.encode(), nonce)


def test_transport_failure_is_unavailable_without_echoing_token_or_error_text(signed, monkeypatch):
    canary = "private-token-or-network-canary"
    def unavailable(_raw):
        raise google_errors.TransportError(canary)
    monkeypatch.setattr(google_identity, "verify_credential", unavailable)
    with pytest.raises(visitor_google.GoogleVerificationUnavailable) as caught:
        visitor_google.verify_credential("synthetic-adapter-token", signed.nonce)
    assert canary not in str(caught.value) and caught.value.__suppress_context__


def test_disabled_configuration_never_skips_the_expected_audience(signed, monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", " ")
    with pytest.raises(visitor_google.GoogleVerificationUnavailable):
        visitor_google.verify_credential(signed.encode(), signed.nonce)
    assert signed.calls == []


def test_verification_does_not_call_staff_login_or_write_any_identity_scope(db, signed, monkeypatch):
    tables = [User, Subscription, GoogleIdentity, GoogleLoginNonce,
              VisitorAccount, VisitorGoogleIdentity, VisitorChallenge, VisitorSession]
    before = [db.query(table).count() for table in tables]
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Visitor verifier called the staff login/signup/nonce flow")
    for name in ["login", "_create_user", "issue_nonce"]:
        monkeypatch.setattr(google_identity, name, forbidden)
    visitor_google.verify_credential(signed.encode(), signed.nonce)
    assert [db.query(table).count() for table in tables] == before


@pytest.mark.parametrize("presenter", ["other-client.apps.googleusercontent.com", "", None, True, 1, [], {}])
def test_present_authorized_party_must_be_the_browser_client(signed, presenter):
    with pytest.raises(visitor_google.InvalidGoogleCredential):
        visitor_google.verify_credential(signed.encode({"azp": presenter}), signed.nonce)


def test_matching_authorized_party_is_accepted(signed):
    result = visitor_google.verify_credential(signed.encode({"azp": settings.google_client_id}), signed.nonce)
    assert result.subject == signed.claims["sub"]


def test_verified_claims_remain_bound_to_the_exact_nonce(signed):
    first = visitor_google.verify_credential(signed.encode(), signed.nonce)
    second_nonce = "vch_" + secrets.token_urlsafe(32)
    second = visitor_google.verify_credential(signed.encode({"nonce": second_nonce}), second_nonce)
    assert first.nonce_hash != second.nonce_hash
    assert second.nonce_hash == hashlib.sha256(second_nonce.encode("ascii")).hexdigest()
    assert not hasattr(second, "nonce")
