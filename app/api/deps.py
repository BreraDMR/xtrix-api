from datetime import timezone
from fastapi import Depends, HTTPException, status, Header
from fastapi.security import OAuth2PasswordBearer
import jwt
from jwt import InvalidTokenError
from sqlalchemy.orm import Session
from app.api.core.config import settings
from app.api.core.i18n import _
from app.api.core.logger import get_logger
from app.api.crud import crud_user
from app.db.session import SessionLocal
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/users/login")
logger = get_logger("auth")
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
def token_revoked(user, payload: dict) -> bool:
    if not user.tokens_valid_after:
        return False
    # the DB keeps naive UTC; tokens from before iat_ms existed count as issued at 0
    valid_after_ms = user.tokens_valid_after.replace(tzinfo=timezone.utc).timestamp() * 1000
    return float(payload.get("iat_ms") or 0) < valid_after_ms
def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
        token_type = payload.get("type")
        if token_type != "access":
            logger.warning(f"Invalid token type: {token_type}")
            raise credentials_exception
        user_id: str | None = payload.get("sub")
        if user_id is None:
            logger.warning("JWT token missing user_id")
            raise credentials_exception
    except InvalidTokenError as e:
        logger.warning(f"JWT validation failed: {str(e)}")
        raise credentials_exception
    user = crud_user.get_active_user(db, int(user_id))
    if user is None:
        logger.warning(f"User not found: id={user_id}")
        raise credentials_exception
    if token_revoked(user, payload):
        logger.warning(f"Revoked access token used: user_id={user_id}")
        raise credentials_exception
    logger.debug(f"User authenticated: id={user_id}, role={user.role}")
    return user
def get_current_user_optional(
    authorization: str | None = Header(None),
    db: Session = Depends(get_db)
):
    if authorization is None or not authorization.startswith("Bearer "):
        return None
    token = authorization.replace("Bearer ", "")
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
        token_type = payload.get("type")
        if token_type != "access":
            return None
        user_id: str | None = payload.get("sub")
        if user_id is None:
            return None
    except InvalidTokenError:
        return None
    user = crud_user.get_active_user(db, int(user_id))
    if user is not None and token_revoked(user, payload):
        return None
    return user
def role_required(*roles: str):
    def checker(
        current_user=Depends(get_current_user),
        accept_language: str | None = Header(default=None, alias="Accept-Language"),
    ):
        if current_user.role not in roles:
            raise HTTPException(
                status_code=403, detail=_("not_allowed", accept_language)
            )
        return current_user
    return checker
def get_locale(accept_language: str | None = Header(default=None, alias="Accept-Language")) -> str:
    from app.api.core.i18n import normalize_locale
    return normalize_locale(accept_language)
def get_portal_session(
    x_portal_token: str | None = Header(default=None, alias="X-Portal-Token"),
    db: Session = Depends(get_db),
    locale: str = Depends(get_locale),
):
    # opaque portal credentials are deliberately not JWTs: staff endpoints keep requiring
    # a CRM access token and cannot be reached with this header
    from app.api.crud import crud_client_portal
    session = crud_client_portal.get_valid_session(db, x_portal_token)
    if session is None:
        raise HTTPException(status_code=401, detail=_("portal_session_invalid", locale))
    return session
