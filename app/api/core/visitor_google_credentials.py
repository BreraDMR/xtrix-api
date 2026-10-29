"""Validate Google's signed claims, without CRM login, nonce consumption or DB writes."""
import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import hmac
import re

from google.auth import exceptions as google_errors
import jwt
from pydantic import EmailStr, TypeAdapter, ValidationError

from app.api.core import google_identity
from app.api.core.clock import utcnow
from app.api.core.config import settings
from app.db.models.visitor_account import GOOGLE_ISSUER, canonical_visitor_email

_NONCE = re.compile(r"vch_[A-Za-z0-9_-]{43}\Z")
_ISSUERS = {GOOGLE_ISSUER, "accounts.google.com"}
_INVALID = "Invalid visitor Google credential"
_UNAVAILABLE = "Visitor Google verification is unavailable"


class InvalidGoogleCredential(ValueError):
    pass


class GoogleVerificationUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class VerifiedGoogleClaims:
    issuer: str
    subject: str = field(repr=False)
    email: str = field(repr=False)
    issued_at: datetime
    expires_at: datetime
    nonce_hash: str = field(repr=False)


def _valid_nonce(value: str) -> bool:
    if not isinstance(value, str) or _NONCE.fullmatch(value) is None:
        return False
    encoded = value[4:]
    decoded = base64.b64decode(encoded + "=", altchars=b"-_", validate=True)
    return len(decoded) == 32 and base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=") == encoded


def require_fresh(claims: VerifiedGoogleClaims) -> None:
    """Call again after the last blocking DB lock, before committing authentication."""
    now = utcnow()
    if (not isinstance(claims, VerifiedGoogleClaims)
            or claims.issued_at > now or claims.expires_at <= now):
        raise InvalidGoogleCredential(_INVALID)


def verify_credential(credential: str, expected_nonce: str) -> VerifiedGoogleClaims:
    """Reuse only signature verification; purpose, origin and replay stay with the caller.

    The caller must consume its visitor challenge in the same transaction as account,
    identity and session writes. This function never calls the CRM login service.
    """
    audience = settings.google_client_id.strip()
    if not audience:
        raise GoogleVerificationUnavailable(_UNAVAILABLE)
    if (not isinstance(credential, str) or not 1 <= len(credential) <= 16384
            or not credential.isascii() or not _valid_nonce(expected_nonce)):
        raise InvalidGoogleCredential(_INVALID)
    try:
        claims = google_identity.verify_credential(credential)
    except google_errors.TransportError:
        raise GoogleVerificationUnavailable(_UNAVAILABLE) from None
    except (ValueError, TypeError, KeyError, google_errors.GoogleAuthError, jwt.PyJWTError):
        raise InvalidGoogleCredential(_INVALID) from None
    try:
        if not isinstance(claims, dict):
            raise ValueError()
        subject, nonce = claims.get("sub"), claims.get("nonce")
        if (claims.get("iss") not in _ISSUERS or claims.get("aud") != audience
                or not isinstance(claims.get("aud"), str)
                or ("azp" in claims and claims["azp"] != audience)
                or claims.get("email_verified") is not True
                or not isinstance(subject, str) or not subject or subject != subject.strip()
                or len(subject) > 255 or not isinstance(nonce, str) or not nonce.isascii()
                or not hmac.compare_digest(nonce, expected_nonce)):
            raise ValueError()
        issued, expires = claims.get("iat"), claims.get("exp")
        if (not isinstance(issued, int) or isinstance(issued, bool)
                or not isinstance(expires, int) or isinstance(expires, bool)
                or issued <= 0 or expires <= issued):
            raise ValueError()
        email = canonical_visitor_email(str(TypeAdapter(EmailStr).validate_python(claims.get("email"))))
        result = VerifiedGoogleClaims(
            issuer=GOOGLE_ISSUER, subject=subject, email=email,
            issued_at=datetime.fromtimestamp(issued, timezone.utc).replace(tzinfo=None),
            expires_at=datetime.fromtimestamp(expires, timezone.utc).replace(tzinfo=None),
            nonce_hash=hashlib.sha256(expected_nonce.encode("ascii")).hexdigest(),
        )
        require_fresh(result)
        return result
    except (ValueError, TypeError, KeyError, OverflowError, OSError, ValidationError):
        raise InvalidGoogleCredential(_INVALID) from None
