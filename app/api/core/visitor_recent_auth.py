"""Recent visitor authentication, independent of CRM sessions and salon access."""
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy.orm import Session

from app.api.core import visitor_sessions
from app.api.core.clock import utcnow

RECENT_AUTH_TTL = timedelta(minutes=5)


class VisitorReauthenticationRequired(ValueError):
    """Sign in again before changing a visitor account's security settings."""


@dataclass(frozen=True)
class RecentVisitorActor:
    account_id: int = field(repr=False)
    session_id: int = field(repr=False)


def require_recent_actor(db: Session, raw_token: str | None,
                         actor: RecentVisitorActor) -> None:
    """Recheck the exact actor while its Account lock is still held.

    Do this after blocking writes, before any deliberate revocation of this session.
    A fixed session creation time is the authentication time; reads never renew it.
    """
    row = visitor_sessions.get_valid_session(db, raw_token)
    now = utcnow()
    if (row is None or row.account_id != actor.account_id or row.id != actor.session_id
            or row.expires_at <= now or row.created_at > now
            or now - row.created_at >= RECENT_AUTH_TTL):
        raise VisitorReauthenticationRequired("A recent visitor login is required")


def lock_recent_actor(db: Session, raw_token: str | None) -> RecentVisitorActor:
    """Resolve, lock Account, then resolve again; caller owns commit or rollback."""
    row = visitor_sessions.get_valid_session(db, raw_token)
    if row is None:
        raise VisitorReauthenticationRequired("A recent visitor login is required")
    actor = RecentVisitorActor(row.account_id, row.id)
    try:
        visitor_sessions.lock_verified_account(db, actor.account_id)
    except visitor_sessions.VisitorAccountUnavailable:
        raise VisitorReauthenticationRequired("A recent visitor login is required") from None
    require_recent_actor(db, raw_token, actor)
    return actor
