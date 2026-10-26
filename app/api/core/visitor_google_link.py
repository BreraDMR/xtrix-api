"""Explicit Google linking: recent visitor auth plus a separate bound Google proof."""
from dataclasses import dataclass, field
import hmac

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.core import visitor_challenges, visitor_google_credentials, visitor_google_login
from app.api.core import visitor_recent_auth
from app.api.core.clock import utcnow
from app.db.models.visitor_account import GOOGLE_ISSUER, VisitorGoogleIdentity


class InvalidGoogleLink(ValueError):
    """The Google credential or purpose-bound proof is unavailable."""


class GoogleIdentityLinkConflict(ValueError):
    """A Google identity cannot be transferred from another visitor."""


@dataclass(frozen=True)
class CompletedGoogleLink:
    account_id: int = field(repr=False)
    created: bool


def _require_live(claims, proof=None) -> None:
    try:
        visitor_google_credentials.require_fresh(claims)
    except visitor_google_credentials.InvalidGoogleCredential:
        raise InvalidGoogleLink("Google linking is not available") from None
    if proof is not None and proof.expires_at <= utcnow():
        raise InvalidGoogleLink("Google linking is not available")


def issue_link_challenge(db: Session, raw_visitor_token: str | None, origin: str):
    """Flush a five-minute actor-bound proof; never commit or contact Google."""
    if not visitor_google_login.is_allowed_origin(origin):
        raise InvalidGoogleLink("Google linking is not available")
    actor = visitor_recent_auth.lock_recent_actor(db, raw_visitor_token)
    issued = visitor_challenges.issue_challenge(db, "google_link", account_id=actor.account_id,
                                                origin=origin)
    visitor_recent_auth.require_recent_actor(db, raw_visitor_token, actor)
    return issued


def link(db: Session, raw_visitor_token: str | None, raw_nonce: str, origin: str,
         verified_claims: visitor_google_credentials.VerifiedGoogleClaims) -> CompletedGoogleLink:
    """Flush a link to the authenticated actor. Caller must commit or whole rollback.

    Verify Google's signature before calling, without holding Account locks. No email
    match grants authority, and every error requires rollback before reusing the DB session.
    """
    claims = verified_claims
    _require_live(claims)
    digest = visitor_challenges._token_hash(raw_nonce)
    if (not visitor_google_login.is_allowed_origin(origin) or digest is None
            or claims.issuer != GOOGLE_ISSUER or not isinstance(claims.nonce_hash, str)
            or not claims.nonce_hash.isascii() or not hmac.compare_digest(digest, claims.nonce_hash)):
        raise InvalidGoogleLink("Google linking is not available")
    actor = visitor_recent_auth.lock_recent_actor(db, raw_visitor_token)
    proof = visitor_challenges.consume_challenge(db, raw_nonce, purpose="google_link",
                                                 account_id=actor.account_id, origin=origin)
    if proof is None:
        raise InvalidGoogleLink("Google linking is not available")
    owner_id = db.query(VisitorGoogleIdentity.account_id).filter(
        VisitorGoogleIdentity.issuer == claims.issuer,
        VisitorGoogleIdentity.subject == claims.subject,
    ).scalar()
    if owner_id is not None and owner_id != actor.account_id:
        raise GoogleIdentityLinkConflict("Google identity is already linked")
    created = owner_id is None
    if created:
        db.add(VisitorGoogleIdentity(issuer=claims.issuer, subject=claims.subject,
                                     account_id=actor.account_id))
        try:
            db.flush()
        except IntegrityError as exc:
            if getattr(getattr(exc.orig, "diag", None), "constraint_name", None) == "visitor_google_identities_pkey":
                raise GoogleIdentityLinkConflict("Google identity is already linked") from None
            raise
    _require_live(claims, proof)
    visitor_recent_auth.require_recent_actor(db, raw_visitor_token, actor)
    return CompletedGoogleLink(actor.account_id, created)
