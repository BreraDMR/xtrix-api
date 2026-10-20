"""Public password reset endpoints.

The request endpoint answers the same way for a known and an unknown email: it must not
tell an anonymous caller which addresses have an account. The token is generated and put
in the email; the API response never contains it.
"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core.i18n import _
from app.api.core.logger import get_logger, mask_email
from app.api.crud import crud_password_reset, crud_user
from app.api.middleware.rate_limit import PASSWORD_RESET_LIMIT, limiter
from app.schemas.password_reset import PasswordResetRequest, PasswordResetRequestOut

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
    user = crud_user.get_by_phone_or_email(db, phone=None, email=email)
    if user is not None and user.deleted_at is None:
        crud_password_reset.issue_token(db, user.id)
    logger.info(f"Password reset requested: email={mask_email(email)}")
    return PasswordResetRequestOut(message=_("password_reset_requested", locale))
