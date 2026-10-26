"""Read the visitor's account and revoke sessions without entering CRM auth."""
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from slowapi.errors import RateLimitExceeded
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import deps, visitor_deps
from app.api.core import error_handlers, visitor_login_guard, visitor_passwords, visitor_registration, visitor_sessions
from app.api.core.i18n import _
from app.api.middleware.rate_limit import LOGIN_LIMIT, PASSWORD_RESET_CONFIRM_LIMIT, client_ip, limiter, rate_limit_exceeded_handler
from app.db.models.visitor_account import VisitorAccount, VisitorGoogleIdentity
from app.db.models.visitor_auth import VisitorSession
from app.schemas.visitor_account import VisitorAccountOut, VisitorLoginIn, VisitorLoginOut, VisitorRegistrationIn


class _NoStoreRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def private_response(request: Request) -> Response:
            try:
                response = await original(request)
            except RequestValidationError as exc:
                response = await error_handlers.validation_exception_handler(request, exc)
            except RateLimitExceeded as exc:
                response = rate_limit_exceeded_handler(request, exc)
            except StarletteHTTPException as exc:
                # Dependency failures use the same no-store policy as successful reads.
                exc.headers = {**(exc.headers or {}), "Cache-Control": "no-store"}
                raise
            response.headers["Cache-Control"] = "no-store"
            return response

        return private_response


router = APIRouter(route_class=_NoStoreRoute)


def _check_login_limit(email: str, ip: str) -> None:
    try:
        visitor_login_guard.check(email, ip)
    except visitor_login_guard.LoginThrottled as exc:
        raise HTTPException(status_code=429, detail="Too many login attempts",
                            headers={"Retry-After": str(exc.retry_after)}) from None


@router.post("/auth/login", response_model=VisitorLoginOut)
@limiter.limit(LOGIN_LIMIT)
def login(request: Request, body: VisitorLoginIn, db: Session = Depends(deps.get_db),
          locale: str = Depends(deps.get_locale)) -> VisitorLoginOut:
    email, ip = body.email, client_ip(request)
    _check_login_limit(email, ip)
    account_id = db.query(VisitorAccount.id).filter(VisitorAccount.email_canonical == email).scalar()
    account = None
    if account_id is not None:
        try:
            account = visitor_sessions.lock_verified_account(db, account_id)
        except visitor_sessions.VisitorAccountUnavailable:
            pass
    # A reset or email change may have committed while this request waited for the lock.
    _check_login_limit(email, ip)
    current_email = account is not None and account.email_canonical == email
    encoded = account.hashed_password if current_email else None
    valid = visitor_passwords.verify_password(body.password.get_secret_value(), encoded)
    if not current_email or not valid:
        visitor_login_guard.failed(email, ip)
        raise HTTPException(status_code=401, detail=_("invalid_credentials", locale))
    session, raw = visitor_sessions.create_session(db, account)
    expires_at = session.expires_at
    db.commit()
    visitor_login_guard.succeeded(email, ip)
    return VisitorLoginOut(session_token=raw, expires_at=expires_at)


@router.post("/auth/register/complete", status_code=201, response_model=VisitorLoginOut)
@limiter.limit(PASSWORD_RESET_CONFIRM_LIMIT)
def complete_registration(request: Request, body: VisitorRegistrationIn,
                          db: Session = Depends(deps.get_db),
                          locale: str = Depends(deps.get_locale)) -> VisitorLoginOut:
    try:
        result = visitor_registration.complete_registration(
            db, body.token.get_secret_value(), body.new_password.get_secret_value())
    except visitor_registration.InvalidRegistrationProof:
        db.rollback()
        raise HTTPException(status_code=400, detail=_("visitor_registration_invalid", locale)) from None
    except visitor_registration.InvalidRegistrationPassword:
        db.rollback()
        raise HTTPException(status_code=422, detail=_("visitor_registration_password_invalid", locale)) from None
    except visitor_registration.RegistrationConflict:
        db.rollback()
        raise HTTPException(status_code=409, detail=_("visitor_registration_unavailable", locale)) from None
    expires_at = result.session.expires_at
    db.commit()
    return VisitorLoginOut(session_token=result.raw_session_token, expires_at=expires_at)


@router.get("/me", response_model=VisitorAccountOut)
def read_account(
    session: VisitorSession = Depends(visitor_deps.get_visitor_session),
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
) -> VisitorAccountOut:
    account = (db.query(VisitorAccount).filter(VisitorAccount.id == session.account_id)
               .populate_existing().one_or_none())
    if account is None:
        raise HTTPException(status_code=401, detail=_("visitor_session_invalid", locale))
    connected = (db.query(VisitorGoogleIdentity.account_id)
                 .filter(VisitorGoogleIdentity.account_id == account.id).first()) is not None
    return VisitorAccountOut(
        id=account.id,
        email=account.email_canonical,
        email_verified_at=account.email_verified_at,
        created_at=account.created_at,
        has_password=account.hashed_password is not None,
        google_connected=connected,
    )


def _logout(db: Session, token: str | None, *, all_sessions: bool) -> Response:
    # An already invalid credential is a completed logout, not an authentication error.
    first = visitor_sessions.get_valid_session(db, token)
    if first is None:
        return Response(status_code=204)
    expected_account_id, expected_session_id = first.account_id, first.id
    try:
        visitor_sessions.lock_verified_account(db, expected_account_id)
    except visitor_sessions.VisitorAccountUnavailable:
        return Response(status_code=204)
    fresh = visitor_sessions.get_valid_session(db, token)
    if (fresh is None or fresh.account_id != expected_account_id
            or fresh.id != expected_session_id):
        return Response(status_code=204)
    if all_sessions:
        visitor_sessions.revoke_all_sessions(db, account_id=expected_account_id)
    else:
        visitor_sessions.revoke_session(db, account_id=expected_account_id,
                                        session_id=expected_session_id)
    db.commit()
    return Response(status_code=204)


@router.post("/auth/logout", status_code=204, response_class=Response)
def logout(
    x_visitor_token: str | None = Header(default=None, alias="X-Visitor-Token"),
    db: Session = Depends(deps.get_db),
) -> Response:
    return _logout(db, x_visitor_token, all_sessions=False)


@router.post("/auth/logout-all", status_code=204, response_class=Response)
def logout_all(
    x_visitor_token: str | None = Header(default=None, alias="X-Visitor-Token"),
    db: Session = Depends(deps.get_db),
) -> Response:
    return _logout(db, x_visitor_token, all_sessions=True)
