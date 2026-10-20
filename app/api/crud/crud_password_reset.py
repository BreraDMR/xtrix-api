"""Password reset tokens: crypto-random, hashed at rest and usable once.

The raw token is returned to the caller exactly once, at issue time, so it can be put in
the email link. The database stores only its SHA-256 digest, which makes a leaked table
row useless. Each new token invalidates the previous unused ones for the same user.
"""
import hashlib
import secrets
from datetime import timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.api.core.clock import utcnow
from app.db.models.password_reset_token import PasswordResetToken

TOKEN_BYTES = 32
TOKEN_TTL = timedelta(minutes=30)


def generate_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def issue_token(
    db: Session, user_id: int, ttl: timedelta = TOKEN_TTL
) -> tuple[PasswordResetToken, str]:
    raw_token = generate_token()
    now = utcnow()
    db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user_id,
        PasswordResetToken.used_at.is_(None),
    ).update({"used_at": now}, synchronize_session=False)
    reset_token = PasswordResetToken(
        user_id=user_id,
        token_hash=hash_token(raw_token),
        expires_at=now + ttl,
    )
    db.add(reset_token)
    db.commit()
    db.refresh(reset_token)
    return reset_token, raw_token


def get_valid_token(
    db: Session, raw_token: str, *, for_update: bool = False
) -> Optional[PasswordResetToken]:
    if not raw_token:
        return None
    query = db.query(PasswordResetToken).filter(
        PasswordResetToken.token_hash == hash_token(raw_token),
        PasswordResetToken.used_at.is_(None),
        PasswordResetToken.expires_at > utcnow(),
    )
    if for_update:
        query = query.populate_existing().with_for_update()
    return query.first()


def mark_token_as_used(db: Session, token: PasswordResetToken, *, commit: bool = True) -> None:
    token.used_at = utcnow()
    if commit:
        db.commit()


def mark_user_tokens_used(db: Session, user_id: int, *, commit: bool = True) -> None:
    db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user_id,
        PasswordResetToken.used_at.is_(None),
    ).update({"used_at": utcnow()}, synchronize_session=False)
    if commit:
        db.commit()
