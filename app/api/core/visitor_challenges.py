"""Purpose-bound visitor proofs. The caller owns the surrounding transaction."""
import base64
import binascii
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import re
import secrets
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.api.core.clock import utcnow
from app.api.core.visitor_sessions import VisitorAccountUnavailable, lock_verified_account
from app.db.models.visitor_account import VisitorAccount, canonical_visitor_email
from app.db.models.visitor_auth import CHALLENGE_PURPOSES, VisitorChallenge

_EMAIL_PURPOSES = {"register", "reset", "email_change"}
_GOOGLE_PURPOSES = {"google_login", "google_link"}
_BOUND_PURPOSES = {"reset", "email_change", "google_link"}
_ACTOR_PURPOSES = {"email_change", "google_link"}
_PREFIX = "vch_"
_TOKEN = re.compile(r"vch_[A-Za-z0-9_-]{43}\Z")


@dataclass(frozen=True)
class IssuedVisitorChallenge:
    raw_token: str = field(repr=False)
    row: VisitorChallenge


def _current(now: datetime | None) -> datetime:
    current = now if now is not None else utcnow()
    return current.astimezone(timezone.utc).replace(tzinfo=None) if current.tzinfo else current


def _account_id(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _origin(value) -> bool:
    if not isinstance(value, str) or not value or len(value) > 2048 or any(c.isspace() for c in value):
        return False
    try:
        parsed = urlsplit(value)
        parsed.port
        return (parsed.scheme in {"http", "https"} and bool(parsed.hostname)
                and parsed.username is None and parsed.password is None
                and value == f"{parsed.scheme}://{parsed.netloc}")
    except ValueError:
        return False


def _token_hash(raw_token) -> str | None:
    if not isinstance(raw_token, str) or len(raw_token) != 47 or _TOKEN.fullmatch(raw_token) is None:
        return None
    encoded = raw_token[len(_PREFIX):]
    try:
        decoded = base64.b64decode(encoded + "=", altchars=b"-_", validate=True)
    except (ValueError, binascii.Error):
        return None
    if len(decoded) != 32 or base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != encoded:
        return None
    return hashlib.sha256(raw_token.encode("ascii")).hexdigest()


def _lock_account(db: Session, account_id: int) -> VisitorAccount | None:
    try:
        return lock_verified_account(db, account_id)
    except VisitorAccountUnavailable:
        return None


def issue_challenge(db: Session, purpose: str, *, email: str | None = None,
                    account_id: int | None = None, origin: str | None = None,
                    now: datetime | None = None) -> IssuedVisitorChallenge:
    """Flush a new digest, without committing or sending anything.

    Call this before dependent account mutations. Bound flows take Account before Challenge,
    matching login/email-change/revocation services. Origin allowlisting belongs to the caller.
    """
    if purpose not in CHALLENGE_PURPOSES:
        raise ValueError("unknown_challenge_purpose")
    bound = purpose in _BOUND_PURPOSES
    if (bound and not _account_id(account_id)) or (not bound and account_id is not None):
        raise ValueError("invalid_challenge_scope")
    if purpose in _EMAIL_PURPOSES:
        if not isinstance(email, str) or origin is not None:
            raise ValueError("invalid_challenge_scope")
        target_email = canonical_visitor_email(email)
    else:
        if email is not None or not _origin(origin):
            raise ValueError("invalid_challenge_scope")
        target_email = None
    with db.no_autoflush:
        account = _lock_account(db, account_id) if bound else None
        if bound and account is None:
            raise ValueError("invalid_challenge_scope")
        if purpose == "reset" and target_email != account.email_canonical:
            raise ValueError("invalid_challenge_scope")
    raw_token = _PREFIX + secrets.token_urlsafe(32)
    digest = _token_hash(raw_token)
    if digest is None:
        raise RuntimeError("invalid_generated_challenge")
    created = _current(now)
    ttl = timedelta(minutes=5 if purpose in _GOOGLE_PURPOSES else 30)
    row = VisitorChallenge(purpose=purpose, token_hash=digest, account_id=account_id,
        email_canonical=target_email, origin=origin, created_at=created, expires_at=created + ttl)
    db.add(row)
    db.flush([row])
    return IssuedVisitorChallenge(raw_token, row)


def consume_challenge(db: Session, raw_token: str | None, *, purpose: str,
                      account_id: int | None = None, origin: str | None = None,
                      now: datetime | None = None) -> VisitorChallenge | None:
    """Consume the exact proof in the caller's transaction, or return None unchanged.

    email_change/google_link require the account from the already authenticated caller.
    An anonymous reset may derive its account from the proof. No caller may substitute email.
    Lock order is Account -> Challenge for bound flows, Challenge only for unbound flows.
    """
    digest = _token_hash(raw_token)
    if digest is None or purpose not in CHALLENGE_PURPOSES:
        return None
    if account_id is not None and not _account_id(account_id):
        return None
    if purpose in _ACTOR_PURPOSES and account_id is None:
        return None
    if purpose not in _BOUND_PURPOSES and account_id is not None:
        return None
    if purpose in _GOOGLE_PURPOSES:
        if not _origin(origin):
            return None
    elif origin is not None:
        return None
    with db.no_autoflush:
        captured_account_id = None
        account = None
        if purpose in _BOUND_PURPOSES:
            # This scalar preflight does not trust a previously cached ORM object or lock proof first.
            preflight = (db.query(VisitorChallenge.account_id)
                         .filter(VisitorChallenge.token_hash == digest, VisitorChallenge.purpose == purpose).first())
            if preflight is None or not _account_id(preflight.account_id):
                return None
            captured_account_id = preflight.account_id
            if account_id is not None and account_id != captured_account_id:
                return None
            account = _lock_account(db, captured_account_id)
            if account is None:
                return None
        row = (db.query(VisitorChallenge).filter(VisitorChallenge.token_hash == digest,
                VisitorChallenge.purpose == purpose, VisitorChallenge.account_id == captured_account_id)
               .populate_existing().with_for_update().first())
        # A proof can expire while we wait for another transaction.
        current = _current(now)
        if row is None or row.consumed_at is not None or row.expires_at <= current:
            return None
        if account_id is not None and row.account_id != account_id:
            return None
        if purpose in _GOOGLE_PURPOSES and row.origin != origin:
            return None
        if purpose == "reset" and row.email_canonical != account.email_canonical:
            return None
        row.consumed_at = current
        db.flush([row])
        return row
