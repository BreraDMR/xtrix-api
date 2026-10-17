from datetime import datetime, timedelta
from typing import Optional, Tuple
from sqlalchemy.orm import Session
from app.api.core.security import (
    get_password_hash, verify_password, create_access_token, 
    create_refresh_token, validate_password_strength
)
from app.api.core.config import settings
from app.api.core.pagination import PaginationParams
from app.db.models.user import User
from app.schemas.user import UserCreate, UserLogin
from app.schemas.refresh_token import RefreshTokenCreate
def create_user(db: Session, user_in: UserCreate, moderation_status: Optional[str] = None) -> User:
    if user_in.password:
        is_valid, error_msg = validate_password_strength(user_in.password)
        if not is_valid:
            raise ValueError(error_msg)
    business_id = None
    if hasattr(user_in, 'business_id') and user_in.business_id:
        from app.db.models.business import Business
        business = db.query(Business).filter(
            Business.id == user_in.business_id,
            Business.deleted_at.is_(None)
        ).first()
        if not business:
            raise ValueError(f"Business with id {user_in.business_id} not found")
        business_id = user_in.business_id
        if user_in.role not in ['specialist', 'employee']:
            raise ValueError("business_id can only be specified for 'specialist' or 'employee' role")
    user = User(
        full_name=user_in.full_name,
        phone=user_in.phone,
        email=user_in.email,
        role=user_in.role,
        specialist_type=user_in.specialist_type if hasattr(user_in, 'specialist_type') else None,
        hashed_password=get_password_hash(user_in.password) if user_in.password else "",
        business_id=business_id,  
        moderation_status=moderation_status or "approved",  
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    if not business_id and user_in.role in ["admin", "specialist"]:
        try:
            from app.api.crud import crud_subscription
            crud_subscription.create_trial_subscription(db, user.id, user_in.role)
            db.commit()
        except Exception as e:
            import logging
            logging.warning(f"Failed to create trial subscription for user {user.id}: {str(e)}")
    return user
def check_account_locked(user: User) -> tuple[bool, Optional[str]]:
    if not user.is_locked:
        return False, None
    if user.locked_until and user.locked_until > datetime.utcnow():
        remaining_minutes = int((user.locked_until - datetime.utcnow()).total_seconds() / 60)
        return True, f"Account is locked. Try again in {remaining_minutes} minutes."
    user.is_locked = False
    user.failed_login_attempts = 0
    user.locked_until = None
    return False, None
def increment_failed_login_attempts(db: Session, user: User) -> None:
    user.failed_login_attempts += 1
    if user.failed_login_attempts >= settings.max_login_attempts:
        user.is_locked = True
        user.locked_until = datetime.utcnow() + timedelta(minutes=settings.lockout_duration_minutes)
    db.commit()
def reset_failed_login_attempts(db: Session, user: User) -> None:
    if user.failed_login_attempts > 0:
        user.failed_login_attempts = 0
        user.is_locked = False
        user.locked_until = None
        db.commit()
def authenticate(db: Session, credentials: UserLogin, totp_token: str | None = None) -> tuple[tuple[str, str] | None, bool]:
    query = db.query(User).filter(User.deleted_at.is_(None))
    if credentials.phone:
        user = query.filter(User.phone == credentials.phone).first()
    elif credentials.email:
        user = query.filter(User.email == credentials.email).first()
    else:
        return None, False
    if not user:
        return None, False
    is_locked, lockout_message = check_account_locked(user)
    if is_locked:
        raise ValueError(lockout_message)
    if not verify_password(credentials.password, user.hashed_password):
        increment_failed_login_attempts(db, user)
        return None, False
    if user.is_2fa_enabled and user.totp_secret:
        if not totp_token:
            return None, True
        from app.api.core.totp import verify_totp_token
        if not verify_totp_token(user.totp_secret, totp_token):
            increment_failed_login_attempts(db, user)
            return None, False
    reset_failed_login_attempts(db, user)
    access_token = create_access_token({"sub": str(user.id), "role": user.role})
    refresh_token_str, expires_at = create_refresh_token({"sub": str(user.id), "role": user.role})
    from app.api.crud.crud_refresh_token import create_refresh_token as create_db_refresh_token
    token_data = RefreshTokenCreate(
        token=refresh_token_str,
        user_id=user.id,
        expires_at=expires_at,
    )
    create_db_refresh_token(db, token_data)
    return (access_token, refresh_token_str), False
def unlock_account(db: Session, user_id: int) -> bool:
    user = get_user(db, user_id)
    if not user:
        return False
    user.is_locked = False
    user.failed_login_attempts = 0
    user.locked_until = None
    db.commit()
    return True
def get_user(db: Session, user_id: int) -> User | None:
    return db.query(User).filter(User.id == user_id).first()
def get_active_user(db: Session, user_id: int) -> User | None:
    # soft-deleted accounts must not authenticate, even with tokens issued before the delete
    return db.query(User).filter(User.id == user_id, User.deleted_at.is_(None)).first()
def get_by_phone_or_email(db: Session, phone: str | None, email: str | None) -> User | None:
    q = db.query(User)
    if phone:
        return q.filter(User.phone == phone).first()
    if email:
        return q.filter(User.email == email).first()
    return None
def list_users_pending_moderation(
    db: Session,
    pagination: Optional[PaginationParams] = None,
) -> Tuple[list[User], int]:
    query = db.query(User).filter(
        User.moderation_status == "pending",
        User.deleted_at.is_(None)
    )
    total = query.count()
    if pagination:
        query = query.offset(pagination.offset).limit(pagination.limit)
    items = query.all()
    return items, total
def moderate_user(
    db: Session,
    user_id: int,
    status: str,
    notes: Optional[str],
    moderated_by_id: int,
) -> Optional[User]:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return None
    user.moderation_status = status
    user.moderation_notes = notes
    user.moderated_at = datetime.utcnow()
    user.moderated_by_id = moderated_by_id
    db.commit()
    db.refresh(user)
    return user
def list_users(
    db: Session,
    role: str | None = None,
    pagination: Optional[PaginationParams] = None,
    viewer: Optional[User] = None,
) -> Tuple[list[User], int]:
    q = db.query(User).filter(User.deleted_at.is_(None))
    if viewer is not None and viewer.role != "admin":
        # everyone but a platform admin only sees people from their own business
        if viewer.business_id:
            q = q.filter(User.business_id == viewer.business_id)
        else:
            q = q.filter(User.id == viewer.id)
    if role:
        q = q.filter(User.role == role)
    total = q.count()
    if pagination:
        q = q.offset(pagination.offset).limit(pagination.limit)
    items = q.all()
    return items, total
