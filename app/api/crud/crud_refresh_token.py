from sqlalchemy.orm import Session
from app.db.models.refresh_token import RefreshToken
from app.schemas.refresh_token import RefreshTokenCreate
from app.api.core.clock import utcnow
def create_refresh_token(db: Session, token_data: RefreshTokenCreate, *, commit: bool = True) -> RefreshToken:
    db_token = RefreshToken(
        token=token_data.token,
        user_id=token_data.user_id,
        expires_at=token_data.expires_at,
    )
    db.add(db_token)
    if commit:
        db.commit()
        db.refresh(db_token)
    return db_token
def get_refresh_token(db: Session, token: str) -> RefreshToken | None:
    return db.query(RefreshToken).filter(
        RefreshToken.token == token,
        RefreshToken.is_revoked == False,
        RefreshToken.expires_at > utcnow(),
    ).first()
def revoke_refresh_token(db: Session, token: str) -> bool:
    db_token = db.query(RefreshToken).filter(RefreshToken.token == token).first()
    if not db_token:
        return False
    db_token.is_revoked = True
    db_token.revoked_at = utcnow()
    db.commit()
    return True
def revoke_all_user_tokens(db: Session, user_id: int, *, commit: bool = True) -> int:
    tokens = db.query(RefreshToken).filter(
        RefreshToken.user_id == user_id,
        RefreshToken.is_revoked == False,
    ).all()
    count = len(tokens)
    for token in tokens:
        token.is_revoked = True
        token.revoked_at = utcnow()
    if commit:
        db.commit()
    return count
def cleanup_expired_tokens(db: Session) -> int:
    now = utcnow()
    expired_tokens = db.query(RefreshToken).filter(
        RefreshToken.expires_at < now
    ).all()
    count = len(expired_tokens)
    for token in expired_tokens:
        db.delete(token)
    db.commit()
    return count
