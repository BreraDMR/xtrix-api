"""Opaque visitor sessions. Authentication and commit belong to the caller.

Callers MUST lock the account before verifying credentials, then issue in that same
transaction. Taking the lock only after an earlier password check is not enough:
a reset could have committed in between. Account locks precede any challenge lock.
A verified account row is not authentication proof. Reads never renew sessions.
"""
import hashlib
import re
import secrets
from datetime import timedelta

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.api.core.clock import utcnow
from app.db.models.visitor_account import VisitorAccount
from app.db.models.visitor_auth import VisitorSession

TOKEN_BYTES = 32
TOKEN_PREFIX = "vst_"
SESSION_TTL = timedelta(days=30)
_TOKEN_PATTERN = re.compile(r"vst_[A-Za-z0-9_-]{43}\Z")


class VisitorAccountUnavailable(ValueError):
    """The visitor account no longer exists or is not verified."""


def _lock_account_id(db: Session, account_id: int) -> int:
    # Mutation helpers must not refresh away the caller's pending password or email.
    with db.no_autoflush:
        found = (db.query(VisitorAccount.id)
                 .filter(VisitorAccount.id == _account_id(account_id),
                         VisitorAccount.email_verified_at.is_not(None))
                 .with_for_update().scalar())
    if found is None:
        raise VisitorAccountUnavailable("Visitor account is not available")
    return found


def _account_id(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("A persisted visitor account id is required")
    return value


def lock_verified_account(db: Session, account_id: int) -> VisitorAccount:
    """Load current account state for the caller's authentication transaction.

    This checks persistence and verified email, not the caller's credentials.
    Password reset, login and account-wide revocation must take this same lock,
    before checking a password or locking a challenge. Commit stays with the caller.
    """
    account_id = _account_id(account_id)
    for pending in db.dirty:
        if (isinstance(pending, VisitorAccount)
                and inspect(pending).identity == (account_id,)
                and db.is_modified(pending)):
            raise ValueError("Lock the visitor account before changing it")
    with db.no_autoflush:
        account = (
            db.query(VisitorAccount)
            .filter(VisitorAccount.id == account_id,
                    VisitorAccount.email_verified_at.is_not(None))
            .populate_existing()
            .with_for_update()
            .one_or_none()
        )
    if account is None:
        raise VisitorAccountUnavailable("Visitor account is not available")
    return account


def create_session(db: Session, account: VisitorAccount) -> tuple[VisitorSession, str]:
    """Flush a session after authentication; the caller commits the whole operation."""
    if not isinstance(account, VisitorAccount):
        raise ValueError("A persisted visitor account is required")
    state = inspect(account)
    if (not state.persistent or state.session is not db
            or state.attrs.email_verified_at.loaded_value is None):
        raise ValueError("A persisted verified visitor account is required")
    account_id = _lock_account_id(db, state.identity[0])
    raw_token = TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_BYTES)
    now = utcnow()
    row = VisitorSession(
        account_id=account_id,
        token_hash=hashlib.sha256(raw_token.encode("ascii")).hexdigest(),
        created_at=now,
        expires_at=now + SESSION_TTL,
    )
    db.add(row)
    db.flush()
    return row, raw_token


def get_valid_session(db: Session, raw_token: str | None) -> VisitorSession | None:
    """Resolve only this namespace and refresh any previously loaded ORM state."""
    if not isinstance(raw_token, str) or _TOKEN_PATTERN.fullmatch(raw_token) is None:
        return None
    digest = hashlib.sha256(raw_token.encode("ascii")).hexdigest()
    return (
        db.query(VisitorSession)
        .join(VisitorAccount, VisitorAccount.id == VisitorSession.account_id)
        .filter(
            VisitorSession.token_hash == digest,
            VisitorSession.revoked_at.is_(None),
            VisitorSession.expires_at > utcnow(),
            VisitorAccount.email_verified_at.is_not(None),
        )
        .populate_existing()
        .one_or_none()
    )


def revoke_session(db: Session, *, account_id: int, session_id: int) -> bool:
    """Revoke one session belonging to this account, without committing.

    A repeat or a foreign session changes nothing. The return value says whether
    this call changed a row, not whether the caller should return an HTTP error.
    """
    locked_id = _lock_account_id(db, account_id)
    changed = db.query(VisitorSession).filter(
        VisitorSession.id == session_id,
        VisitorSession.account_id == locked_id,
        VisitorSession.revoked_at.is_(None),
    ).update({"revoked_at": utcnow()}, synchronize_session="fetch")
    db.flush()
    return bool(changed)


def revoke_all_sessions(db: Session, *, account_id: int) -> int:
    """Revoke this account's sessions, so reset can commit the password atomically."""
    locked_id = _lock_account_id(db, account_id)
    changed = db.query(VisitorSession).filter(
        VisitorSession.account_id == locked_id,
        VisitorSession.revoked_at.is_(None),
    ).update({"revoked_at": utcnow()}, synchronize_session="fetch")
    db.flush()
    return changed
