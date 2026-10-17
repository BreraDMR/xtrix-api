from datetime import timedelta
from typing import Optional
import secrets
from sqlalchemy.orm import Session
from app.db.models.password_reset_token import PasswordResetToken
from app.api.core.clock import utcnow
def create_password_reset_token(db: Session, user_id: int, expires_in_hours: int = 1) -> PasswordResetToken:
    token = secrets.token_urlsafe(32)
    code = str(secrets.randbelow(900000) + 100000)  
    expires_at = utcnow() + timedelta(hours=expires_in_hours)
    db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user_id,
        PasswordResetToken.used_at.is_(None),
        PasswordResetToken.expires_at > utcnow()
    ).update({"used_at": utcnow()})
    reset_token = PasswordResetToken(
        user_id=user_id,
        token=token,
        code=code,
        expires_at=expires_at,
    )
    db.add(reset_token)
    db.commit()
    db.refresh(reset_token)
    return reset_token
def get_reset_token_by_token(db: Session, token: str) -> Optional[PasswordResetToken]:
    return db.query(PasswordResetToken).filter(
        PasswordResetToken.token == token,
        PasswordResetToken.used_at.is_(None),
        PasswordResetToken.expires_at > utcnow()
    ).first()
def get_reset_token_by_code(db: Session, code: str) -> Optional[PasswordResetToken]:
    return db.query(PasswordResetToken).filter(
        PasswordResetToken.code == code,
        PasswordResetToken.used_at.is_(None),
        PasswordResetToken.expires_at > utcnow()
    ).first()
def mark_token_as_used(db: Session, token: PasswordResetToken) -> None:
    token.used_at = utcnow()
    db.commit()
