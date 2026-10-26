"""Google visitor login transaction; caller owns the final commit or whole rollback."""
from dataclasses import dataclass, field
from datetime import datetime
import hmac

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.core import visitor_challenges, visitor_google_credentials, visitor_sessions
from app.api.core.clock import utcnow
from app.api.core.config import settings
from app.db.models.visitor_account import GOOGLE_ISSUER, VisitorAccount, VisitorGoogleIdentity


class InvalidGoogleLogin(ValueError):
    """The proof or verified credential is no longer valid for this login."""


class GoogleEmailCollision(ValueError):
    """Sign in to the existing visitor and explicitly link Google instead."""


class GoogleLoginConflict(ValueError):
    """Identity state raced; the caller must roll back before trying again."""


@dataclass(frozen=True)
class CompletedGoogleLogin:
    account_id: int = field(repr=False)
    session_id: int = field(repr=False)
    raw_session_token: str = field(repr=False)
    expires_at: datetime


def _identity_account(db: Session, claims, *, lock=False):
    query = db.query(VisitorGoogleIdentity.account_id).filter(
        VisitorGoogleIdentity.issuer == claims.issuer,
        VisitorGoogleIdentity.subject == claims.subject,
    )
    if lock:
        query = query.with_for_update(of=VisitorGoogleIdentity)
    return query.scalar()


def is_allowed_origin(origin: str | None) -> bool:
    # No wildcard, Host header, path trimming or caller-provided callback URL.
    return (visitor_challenges._origin(origin)
            and origin in [settings.frontend_url, *settings.get_cors_origins_list()])


def _require_live(claims, proof=None):
    try:
        visitor_google_credentials.require_fresh(claims)
    except visitor_google_credentials.InvalidGoogleCredential:
        raise InvalidGoogleLogin("Google login is not available") from None
    if proof is not None and proof.expires_at <= utcnow():
        raise InvalidGoogleLogin("Google login is not available")


def login(db: Session, raw_nonce: str, origin: str,
          verified_claims: visitor_google_credentials.VerifiedGoogleClaims) -> CompletedGoogleLogin:
    """Flush authentication only. Call with a clean request transaction.

    Verify Google's signature before entering this service. Never send its returned
    credential before commit. Every exception requires a whole caller rollback,
    including typed conflicts: this also restores the consumed proof.
    """
    claims = verified_claims
    _require_live(claims)
    digest = visitor_challenges._token_hash(raw_nonce)
    if (not is_allowed_origin(origin) or digest is None or claims.issuer != GOOGLE_ISSUER
            or not isinstance(claims.nonce_hash, str) or not claims.nonce_hash.isascii()
            or len(claims.nonce_hash) != 64
            or not hmac.compare_digest(digest, claims.nonce_hash)):
        raise InvalidGoogleLogin("Google login is not available")
    with db.no_autoflush:
        account_id = _identity_account(db, claims)
    if account_id is not None:
        try:
            account = visitor_sessions.lock_verified_account(db, account_id)
        except visitor_sessions.VisitorAccountUnavailable:
            raise InvalidGoogleLogin("Google login is not available") from None
        if _identity_account(db, claims, lock=True) != account_id:
            raise GoogleLoginConflict("Google login state changed")
        proof = visitor_challenges.consume_challenge(db, raw_nonce, purpose="google_login", origin=origin)
        if proof is None:
            raise InvalidGoogleLogin("Google login is not available")
    else:
        proof = visitor_challenges.consume_challenge(db, raw_nonce, purpose="google_login", origin=origin)
        if proof is None:
            raise InvalidGoogleLogin("Google login is not available")
        if db.query(VisitorAccount.id).filter(VisitorAccount.email_canonical == claims.email).scalar() is not None:
            raise GoogleEmailCollision("Explicit Google linking is required")
        try:
            # No account is locked or adopted by email. Only our NEW row is written.
            account = VisitorAccount(email_canonical=claims.email,
                email_verified_at=utcnow(), hashed_password=None)
            db.add(account)
            db.flush([account])
            db.add(VisitorGoogleIdentity(issuer=claims.issuer, subject=claims.subject,
                                         account_id=account.id))
            db.flush()
        except IntegrityError as exc:
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint in {"uq_visitor_accounts_email_canonical", "visitor_google_identities_pkey"}:
                raise GoogleLoginConflict("Google login state changed") from None
            raise
    # Unique inserts, the account lock and the proof lock may all have waited.
    _require_live(claims, proof)
    session, raw = visitor_sessions.create_session(db, account)
    _require_live(claims, proof)
    return CompletedGoogleLogin(account.id, session.id, raw, session.expires_at)
