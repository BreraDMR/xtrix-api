"""Issue and resolve visitor-portal credentials.

Magic links are single-use and short-lived; sessions are opaque, revocable and scoped
to one business+client pair. Only digests are stored, so a leaked database row cannot
be replayed as a credential.
"""
import hashlib
import secrets
from datetime import timedelta
from typing import Optional

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.api.core.clock import utcnow
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.client_business_relation import ClientBusinessRelation
from app.db.models.client_portal import ClientPortalSession, ClientPortalToken

MAGIC_TOKEN_BYTES = 32
MAGIC_TTL = timedelta(minutes=30)
SESSION_TTL = timedelta(days=30)


def _hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def issue_magic_token(db: Session, business_id: int, client_id: int) -> str:
    now = utcnow()
    db.query(ClientPortalToken).filter(
        ClientPortalToken.business_id == business_id,
        ClientPortalToken.client_id == client_id,
        ClientPortalToken.used_at.is_(None),
    ).update({"used_at": now}, synchronize_session=False)
    raw_token = secrets.token_urlsafe(MAGIC_TOKEN_BYTES)
    db.add(
        ClientPortalToken(
            business_id=business_id,
            client_id=client_id,
            token_hash=_hash(raw_token),
            expires_at=now + MAGIC_TTL,
        )
    )
    db.commit()
    return raw_token


def consume_magic_token(db: Session, raw_token: str) -> Optional[ClientPortalToken]:
    """Lock and mark the link as used; the caller commits together with the new session."""
    if not raw_token:
        return None
    token = (
        db.query(ClientPortalToken)
        .filter(
            ClientPortalToken.token_hash == _hash(raw_token),
            ClientPortalToken.used_at.is_(None),
            ClientPortalToken.expires_at > utcnow(),
        )
        .populate_existing()
        .with_for_update()
        .first()
    )
    if token is None:
        return None
    from app.api.core.subscription_access import enforce_business_access
    enforce_business_access(db, token.business_id)
    token.used_at = utcnow()
    db.flush()
    return token


def create_session(db: Session, business_id: int, client_id: int) -> tuple[ClientPortalSession, str]:
    raw_token = secrets.token_urlsafe(MAGIC_TOKEN_BYTES)
    session = ClientPortalSession(
        business_id=business_id,
        client_id=client_id,
        token_hash=_hash(raw_token),
        expires_at=utcnow() + SESSION_TTL,
    )
    db.add(session)
    db.flush()
    return session, raw_token


def get_valid_session(db: Session, raw_token: Optional[str]) -> Optional[ClientPortalSession]:
    if not raw_token or len(raw_token) > 128:
        return None
    return (
        db.query(ClientPortalSession)
        .join(Client, Client.id == ClientPortalSession.client_id)
        .join(
            ClientBusinessRelation,
            and_(
                ClientBusinessRelation.client_id == ClientPortalSession.client_id,
                ClientBusinessRelation.business_id == ClientPortalSession.business_id,
            ),
        )
        .join(Business, Business.id == ClientPortalSession.business_id)
        .filter(
            ClientPortalSession.token_hash == _hash(raw_token),
            ClientPortalSession.revoked_at.is_(None),
            ClientPortalSession.expires_at > utcnow(),
            Client.deleted_at.is_(None),
            Business.deleted_at.is_(None),
        )
        .populate_existing()
        .first()
    )


def revoke_session(db: Session, session: ClientPortalSession) -> None:
    if session.revoked_at is None:
        session.revoked_at = utcnow()
        db.commit()
