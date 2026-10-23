"""Public password reset endpoints.

The request endpoint answers the same way for a known and an unknown email: it must not
tell an anonymous caller which addresses have an account. The token is generated and put
in the email; the API response never contains it.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core import password_reset_email
from app.api.core.clock import utcnow
from app.api.core.i18n import _
from app.api.core.logger import get_logger, mask_email
from app.api.core.security import get_password_hash
from app.api.crud import crud_password_reset, crud_refresh_token, crud_user
from app.api.middleware.rate_limit import (
    PASSWORD_RESET_CONFIRM_LIMIT,
    PASSWORD_RESET_LIMIT,
    limiter,
)
from app.schemas.password_reset import (
    PasswordResetConfirm,
    PasswordResetConfirmOut,
    PasswordResetRequest,
    PasswordResetRequestOut,
)

router = APIRouter()
logger = get_logger("password_reset")


@router.post("/password-reset/request", response_model=PasswordResetRequestOut)
@limiter.limit(PASSWORD_RESET_LIMIT)
def request_password_reset(
    request: Request,
    data: PasswordResetRequest,
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    email = str(data.email)
    user = crud_user.get_password_reset_user(db, email)
    if user is not None and user.deleted_at is None:
        password_reset_email.queue_password_reset(db, user, locale)
        db.commit()
    logger.info(f"Password reset requested: email={mask_email(email)}")
    return PasswordResetRequestOut(message=_("password_reset_requested", locale))


@router.post("/password-reset/confirm", response_model=PasswordResetConfirmOut)
@limiter.limit(PASSWORD_RESET_CONFIRM_LIMIT)
def confirm_password_reset(
    request: Request,
    data: PasswordResetConfirm,
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    # the row lock makes two parallel confirmations of the same link impossible
    reset_token = crud_password_reset.get_valid_token(db, data.token, for_update=True)
    if reset_token is None:
        raise HTTPException(status_code=400, detail=_("password_reset_invalid_token", locale))
    user = crud_user.get_active_user(db, reset_token.user_id, for_update=True)
    if user is None:
        raise HTTPException(status_code=400, detail=_("password_reset_invalid_token", locale))
    user.hashed_password = get_password_hash(data.new_password)
    # every access and refresh token issued before this moment stops working
    user.tokens_valid_after = utcnow()
    crud_refresh_token.revoke_all_user_tokens(db, user.id, commit=False)
    crud_password_reset.mark_user_tokens_used(db, user.id, commit=False)
    db.commit()
    logger.info(f"Password reset completed for user_id={user.id}")
    return PasswordResetConfirmOut(message=_("password_reset_success", locale))
