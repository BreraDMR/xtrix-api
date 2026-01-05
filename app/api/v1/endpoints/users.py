from fastapi import APIRouter, Depends, HTTPException, Request, Body, Query
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core.i18n import _, localize_response
from app.api.core.config import settings
from app.api.core.logger import get_logger
from app.api.core.security import verify_refresh_token, create_access_token, create_refresh_token
from app.api.core.pagination import PaginationParams, PaginatedResponse
if settings.rate_limit_enabled:
    from app.api.middleware.rate_limit import limiter, get_rate_limit_for_login
else:
    # Создаем пустой декоратор, если rate limiting отключен
    class DummyLimiter:
        def limit(self, *args, **kwargs):
            def decorator(f):
                return f
            return decorator
    limiter = DummyLimiter()
    def get_rate_limit_for_login():
        return None
from app.api.crud import crud_user, crud_refresh_token
from app.db.models.user import User
from app.schemas.user import (
    UserCreate, UserLogin, UserOut, TwoFactorSetup, 
    TwoFactorVerify, TwoFactorEnable, LoginResponse,
    ModerationRequest, ModerationResponse
)
from app.schemas.refresh_token import TokenRefresh, TokenResponse, RefreshTokenCreate

router = APIRouter()
logger = get_logger("users")


@router.post("/", response_model=UserOut)
def create_user(
    user: UserCreate,
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    logger.info(f"Creating user: phone={user.phone}, email={user.email}")
    if crud_user.get_by_phone_or_email(db, phone=user.phone, email=None):
        logger.warning(f"User creation failed: phone {user.phone} already exists")
        raise HTTPException(status_code=400, detail=_("phone_exists", locale))
    if user.email and crud_user.get_by_phone_or_email(db, phone=None, email=user.email):
        logger.warning(f"User creation failed: email {user.email} already exists")
        raise HTTPException(status_code=400, detail=_("email_exists", locale))
    result = crud_user.create_user(db, user)
    logger.info(f"User created successfully: id={result.id}, phone={result.phone}")
    return localize_response(result.dict(), locale) if hasattr(result, 'dict') else result


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="User login",
    description="Authenticate user and receive access and refresh tokens. If 2FA is enabled, requires_2fa will be true and user must provide TOTP token. Rate limited to 5 requests per minute.",
    response_description="Authentication tokens or 2FA requirement",
    responses={
        200: {
            "description": "Successful login or 2FA required",
        },
        401: {"description": "Invalid credentials"},
        423: {"description": "Account is locked"},
        429: {"description": "Rate limit exceeded"},
    }
)
@limiter.limit(get_rate_limit_for_login)
def login(
    request: Request,
    credentials: UserLogin,
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    logger.info(f"Login attempt: email={credentials.email or 'N/A'}, phone={credentials.phone or 'N/A'}, has_2fa_token={credentials.totp_token is not None}")
    try:
        result = crud_user.authenticate(db, credentials, credentials.totp_token)
        access_token_or_none, requires_2fa_flag = result
        
        if access_token_or_none is None:
            if requires_2fa_flag:  # requires_2fa
                logger.info(f"Login requires 2FA: email={credentials.email or 'N/A'}, phone={credentials.phone or 'N/A'}")
                return LoginResponse(requires_2fa=True)
            else:
                logger.warning(f"Failed login attempt: email={credentials.email or 'N/A'}, phone={credentials.phone or 'N/A'}, ip={request.client.host if request.client else 'unknown'}")
                raise HTTPException(status_code=401, detail=_("invalid_credentials", locale))
        
        access_token, refresh_token = access_token_or_none
        logger.info(f"Successful login: email={credentials.email or 'N/A'}, phone={credentials.phone or 'N/A'}")
        return LoginResponse(
            requires_2fa=False,
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer"
        )
    except ValueError as e:
        # Account locked
        logger.warning(f"Login blocked: {str(e)}")
        raise HTTPException(status_code=423, detail=str(e))


@router.get("/me", response_model=UserOut)
def get_current_user_info(
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    """Get current authenticated user info."""
    result = current_user
    if hasattr(result, 'model_dump'):
        return localize_response(result.model_dump(), locale)
    elif hasattr(result, 'dict'):
        return localize_response(result.dict(), locale)
    return localize_response(result, locale)


@router.post("/refresh", response_model=TokenResponse)
def refresh_token(
    token_data: TokenRefresh = Body(...),
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    """Refresh access token using refresh token."""
    logger.info("Token refresh attempt")
    
    # Verify refresh token
    payload = verify_refresh_token(token_data.refresh_token)
    if not payload:
        logger.warning("Invalid refresh token provided")
        raise HTTPException(status_code=401, detail=_("invalid_refresh_token", locale))
    
    # Check if token exists in database and is not revoked
    db_token = crud_refresh_token.get_refresh_token(db, token_data.refresh_token)
    if not db_token:
        logger.warning("Refresh token not found in database or revoked")
        raise HTTPException(status_code=401, detail=_("invalid_refresh_token", locale))
    
    # Get user
    user_id = int(payload.get("sub"))
    user = crud_user.get_user(db, user_id)
    if not user:
        logger.warning(f"User not found for refresh token: user_id={user_id}")
        raise HTTPException(status_code=401, detail=_("user_not_found", locale))
    
    # Create new access token
    new_access_token = create_access_token({"sub": str(user.id), "role": user.role})
    
    # Optionally create new refresh token (rotate refresh token)
    new_refresh_token_str, expires_at = create_refresh_token({"sub": str(user.id), "role": user.role})
    
    # Revoke old refresh token
    crud_refresh_token.revoke_refresh_token(db, token_data.refresh_token)
    
    # Store new refresh token
    new_token_data = RefreshTokenCreate(
        token=new_refresh_token_str,
        user_id=user.id,
        expires_at=expires_at,
    )
    crud_refresh_token.create_refresh_token(db, new_token_data)
    
    logger.info(f"Token refreshed successfully for user_id={user_id}")
    return TokenResponse(
        access_token=new_access_token,
        refresh_token=new_refresh_token_str,
        token_type="bearer"
    )


@router.post("/logout")
def logout(
    token_data: TokenRefresh = Body(...),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    """Revoke a refresh token (logout)."""
    logger.info(f"Logout attempt for user_id={current_user.id}")
    
    # Verify the refresh token belongs to the current user
    payload = verify_refresh_token(token_data.refresh_token)
    if not payload:
        logger.warning("Invalid refresh token provided for logout")
        raise HTTPException(status_code=401, detail=_("invalid_refresh_token", locale))
    
    token_user_id = int(payload.get("sub"))
    if token_user_id != current_user.id:
        logger.warning(f"Refresh token user_id mismatch: token_user={token_user_id}, current_user={current_user.id}")
        raise HTTPException(status_code=403, detail=_("forbidden", locale))
    
    # Revoke the refresh token
    revoked = crud_refresh_token.revoke_refresh_token(db, token_data.refresh_token)
    if not revoked:
        logger.warning(f"Refresh token not found for revocation: user_id={current_user.id}")
        raise HTTPException(status_code=404, detail=_("refresh_token_not_found", locale))
    
    logger.info(f"User logged out successfully: user_id={current_user.id}")
    return {"message": _("logout_success", locale)}


@router.post("/logout-all")
def logout_all(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    """Revoke all refresh tokens for the current user."""
    logger.info(f"Logout all devices for user_id={current_user.id}")
    
    count = crud_refresh_token.revoke_all_user_tokens(db, current_user.id)
    logger.info(f"Revoked {count} refresh tokens for user_id={current_user.id}")
    
    return {"message": _("logout_all_success", locale), "revoked_count": count}


@router.post("/{user_id}/unlock")
def unlock_user_account(
    user_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin")),
    locale: str = Depends(deps.get_locale),
):
    """Unlock a user account (admin only)."""
    logger.info(f"Unlock account request: user_id={user_id}, admin_id={current_user.id}")
    success = crud_user.unlock_account(db, user_id)
    if not success:
        raise HTTPException(status_code=404, detail=_("user_not_found", locale))
    logger.info(f"Account unlocked successfully: user_id={user_id}")
    return {"message": _("account_unlocked", locale)}


@router.get("/pending", response_model=PaginatedResponse[UserOut], summary="List users pending moderation")
def list_pending_users(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin")),
    locale: str = Depends(deps.get_locale),
):
    """Получить список пользователей, ожидающих модерации (только для админа)."""
    pagination = PaginationParams(page=page, per_page=per_page)
    users, total = crud_user.list_users_pending_moderation(db, pagination)
    
    localized_items = [
        localize_response(
            u.model_dump() if hasattr(u, 'model_dump') else (u.dict() if hasattr(u, 'dict') else u),
            locale
        )
        for u in users
    ]
    
    return PaginatedResponse.create(
        items=localized_items,
        total=total,
        page=page,
        per_page=per_page,
    )


@router.post("/{user_id}/moderate", response_model=ModerationResponse, summary="Moderate user account")
def moderate_user(
    user_id: int,
    moderation: ModerationRequest,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin")),
    locale: str = Depends(deps.get_locale),
):
    """Модерировать пользователя: одобрить или отклонить (только для админа)."""
    logger.info(f"Moderation request: user_id={user_id}, status={moderation.status}, admin_id={current_user.id}")
    
    user = crud_user.moderate_user(
        db=db,
        user_id=user_id,
        status=moderation.status,
        notes=moderation.notes,
        moderated_by_id=current_user.id,
    )
    
    if not user:
        raise HTTPException(status_code=404, detail=_("user_not_found", locale))
    
    logger.info(f"User moderated successfully: user_id={user_id}, status={moderation.status}")
    
    return ModerationResponse(
        id=user.id,
        moderation_status=user.moderation_status,
        moderation_notes=user.moderation_notes,
        moderated_at=user.moderated_at,
        moderated_by_id=user.moderated_by_id,
    )


@router.get("/", response_model=PaginatedResponse[UserOut])
def users_list(
    role: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner")),
    locale: str = Depends(deps.get_locale),
):
    pagination = PaginationParams(page=page, per_page=per_page)
    users, total = crud_user.list_users(db, role, pagination)
    
    localized_items = [
        localize_response(
            u.model_dump() if hasattr(u, 'model_dump') else (u.dict() if hasattr(u, 'dict') else u),
            locale
        )
        for u in users
    ]
    
    return PaginatedResponse.create(
        items=localized_items,
        total=total,
        page=page,
        per_page=per_page,
    )


@router.post("/2fa/setup", response_model=TwoFactorSetup)
def setup_2fa(
    current_user: User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    """Generate a new TOTP secret and QR code for 2FA setup."""
    from app.api.core.totp import generate_totp_secret, generate_totp_qr_code
    
    logger.info(f"2FA setup requested for user_id={current_user.id}")
    
    # Generate new secret
    secret = generate_totp_secret()
    
    # Store secret temporarily (not enabled yet)
    current_user.totp_secret = secret
    current_user.is_2fa_enabled = False
    db.commit()
    
    # Generate QR code
    email_or_phone = current_user.email or current_user.phone or f"user_{current_user.id}"
    qr_code = generate_totp_qr_code(email_or_phone, secret)
    
    logger.info(f"2FA secret generated for user_id={current_user.id}")
    return TwoFactorSetup(secret=secret, qr_code=qr_code)


@router.post("/2fa/enable", response_model=dict)
def enable_2fa(
    data: TwoFactorEnable,
    current_user: User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    """Enable 2FA by verifying TOTP token."""
    from app.api.core.totp import verify_totp_token
    
    logger.info(f"2FA enable attempt for user_id={current_user.id}")
    
    if not current_user.totp_secret:
        raise HTTPException(status_code=400, detail=_("2fa_not_setup", locale) or "2FA not set up. Please run /2fa/setup first.")
    
    # Verify token
    if not verify_totp_token(current_user.totp_secret, data.token):
        logger.warning(f"Invalid 2FA token for user_id={current_user.id}")
        raise HTTPException(status_code=400, detail=_("invalid_2fa_token", locale) or "Invalid 2FA token")
    
    # Enable 2FA
    current_user.is_2fa_enabled = True
    db.commit()
    
    logger.info(f"2FA enabled for user_id={current_user.id}")
    return {"message": _("2fa_enabled", locale) or "2FA enabled successfully", "is_2fa_enabled": True}


@router.post("/2fa/disable", response_model=dict)
def disable_2fa(
    data: TwoFactorVerify,
    current_user: User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    """Disable 2FA by verifying TOTP token."""
    from app.api.core.totp import verify_totp_token
    
    logger.info(f"2FA disable attempt for user_id={current_user.id}")
    
    if not current_user.is_2fa_enabled or not current_user.totp_secret:
        raise HTTPException(status_code=400, detail=_("2fa_not_enabled", locale) or "2FA is not enabled")
    
    # Verify token
    if not verify_totp_token(current_user.totp_secret, data.token):
        logger.warning(f"Invalid 2FA token for disabling 2FA for user_id={current_user.id}")
        raise HTTPException(status_code=400, detail=_("invalid_2fa_token", locale) or "Invalid 2FA token")
    
    # Disable 2FA and clear secret
    current_user.is_2fa_enabled = False
    current_user.totp_secret = None
    db.commit()
    
    logger.info(f"2FA disabled for user_id={current_user.id}")
    return {"message": _("2fa_disabled", locale) or "2FA disabled successfully", "is_2fa_enabled": False}


@router.get("/2fa/status", response_model=dict)
def get_2fa_status(
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    """Get 2FA status for current user."""
    return {
        "is_2fa_enabled": current_user.is_2fa_enabled or False,
    }
