"""Issue, resolve and revoke visit-scoped management tokens.

A guest link carries one raw token; the database keeps only its SHA-256 hash. The
token names one visit and one business, so it can't be pointed at another booking,
and it stays usable until a week after the visit unless revoked first.
"""
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from app.api.core.clock import utcnow
from app.db.models.booking_manage_token import BookingManageToken
from app.db.models.visit import Visit

POST_VISIT_GRACE = timedelta(days=7)
MIN_TOKEN_TTL = timedelta(days=7)
TOKEN_BYTES = 32
TOKEN_MAX_LENGTH = 128
ALL_ACTIONS = ("confirm", "reschedule", "cancel")


class BookingManageTokenInvalid(Exception):
    """Unknown, expired, revoked, out of scope, or no longer attached to a live visit."""


def _hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _actions_column(actions: Optional[Iterable[str]]) -> str:
    selected = set(ALL_ACTIONS if actions is None else actions)
    ordered = [action for action in ALL_ACTIONS if action in selected]
    if not ordered:
        raise ValueError("a management token needs at least one action")
    return ",".join(ordered)


def token_allows(token: BookingManageToken, action: str) -> bool:
    return action in token.actions.split(",")


def token_expiry(visit: Visit, now: Optional[datetime] = None) -> datetime:
    """Long enough for a booking made far ahead, still bounded after the visit."""
    current = now or utcnow()
    return max(visit.date_start + POST_VISIT_GRACE, current + MIN_TOKEN_TTL)


def issue_token(
    db: Session,
    visit: Visit,
    ttl: Optional[timedelta] = None,
    actions: Optional[Iterable[str]] = None,
) -> tuple[str, BookingManageToken]:
    if not visit.business_id:
        raise ValueError("a management token needs a business-scoped visit")
    raw = secrets.token_urlsafe(TOKEN_BYTES)
    token = BookingManageToken(
        business_id=visit.business_id,
        visit_id=visit.id,
        token_hash=_hash(raw),
        actions=_actions_column(actions),
        expires_at=utcnow() + ttl if ttl is not None else token_expiry(visit),
    )
    db.add(token)
    db.commit()
    db.refresh(token)
    return raw, token


def resolve_token(
    db: Session, raw_token: Optional[str], action: Optional[str] = None
) -> tuple[BookingManageToken, Visit]:
    if not raw_token or len(raw_token) > TOKEN_MAX_LENGTH:
        raise BookingManageTokenInvalid
    token = (
        db.query(BookingManageToken)
        .filter(BookingManageToken.token_hash == _hash(raw_token))
        .first()
    )
    if not token or token.revoked_at is not None or token.expires_at <= utcnow():
        raise BookingManageTokenInvalid
    if action is not None and not token_allows(token, action):
        raise BookingManageTokenInvalid
    visit = (
        db.query(Visit)
        .filter(Visit.id == token.visit_id, Visit.deleted_at.is_(None))
        .first()
    )
    if not visit or visit.business_id != token.business_id:
        raise BookingManageTokenInvalid
    return token, visit


def revoke_token(db: Session, token: BookingManageToken) -> None:
    if token.revoked_at is None:
        token.revoked_at = utcnow()
        db.commit()


def revoke_visit_tokens(db: Session, visit_id: int) -> int:
    count = (
        db.query(BookingManageToken)
        .filter(
            BookingManageToken.visit_id == visit_id,
            BookingManageToken.revoked_at.is_(None),
        )
        .update({BookingManageToken.revoked_at: utcnow()}, synchronize_session=False)
    )
    db.commit()
    return count
