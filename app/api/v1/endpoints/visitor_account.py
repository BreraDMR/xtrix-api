"""Visitor identity and sessions stay independent from CRM authentication."""
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from slowapi.errors import RateLimitExceeded
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import deps, visitor_deps
from app.api.core import error_handlers, visitor_challenges, visitor_google_credentials, visitor_google_login
from app.api.core import visitor_login_guard, visitor_passwords, visitor_registration, visitor_sessions
from app.api.core import visitor_registration_mail, visitor_password_reset
from app.api.core import visitor_google_link, visitor_recent_auth
from app.api.core.config import settings
from app.api.core.i18n import _
from app.api.middleware.rate_limit import LOGIN_LIMIT, PASSWORD_RESET_CONFIRM_LIMIT, SIGNUP_LIMIT, client_ip, limiter, rate_limit_exceeded_handler
from app.db.models.visitor_account import VisitorAccount, VisitorGoogleIdentity
from app.db.models.visitor_auth import VisitorSession
from app.schemas.visitor_account import VisitorAccountOut, VisitorLoginIn, VisitorLoginOut, VisitorRegistrationIn
from app.schemas.visitor_account import VisitorGoogleLoginIn, VisitorGoogleNonceIn
from app.schemas.visitor_account import VisitorGoogleLinkIn, VisitorPasswordResetIn
from app.schemas.visitor_account import VisitorRegistrationRequestIn, VisitorRegistrationRequestOut
from app.schemas.google_identity import GoogleConfigOut, GoogleNonceOut


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


def _google_origin(request: Request, locale: str) -> str:
    if not settings.google_client_id.strip():
        raise HTTPException(status_code=503, detail=_("visitor_google_unavailable", locale))
    origin = request.headers.get("origin")
    if not visitor_google_login.is_allowed_origin(origin):
        raise HTTPException(status_code=403, detail=_("visitor_google_origin_invalid", locale))
    return origin


@router.get("/auth/google/config", response_model=GoogleConfigOut)
def google_config() -> GoogleConfigOut:
    client_id = settings.google_client_id.strip()
    return GoogleConfigOut(enabled=bool(client_id), client_id=client_id or None)


@router.post("/auth/google/nonce", response_model=GoogleNonceOut)
@limiter.limit(LOGIN_LIMIT)
def google_nonce(request: Request, body: VisitorGoogleNonceIn,
                 db: Session = Depends(deps.get_db),
                 locale: str = Depends(deps.get_locale)) -> GoogleNonceOut:
    origin = _google_origin(request, locale)
    proof = visitor_challenges.issue_challenge(db, "google_login", origin=origin)
    expires_in = int((proof.row.expires_at - proof.row.created_at).total_seconds())
    db.commit()
    return GoogleNonceOut(nonce=proof.raw_token, expires_in=expires_in)


@router.post("/auth/google/login", response_model=VisitorLoginOut)
@limiter.limit(LOGIN_LIMIT)
def google_login(request: Request, body: VisitorGoogleLoginIn,
                 db: Session = Depends(deps.get_db),
                 locale: str = Depends(deps.get_locale)) -> VisitorLoginOut:
    origin = _google_origin(request, locale)
    raw_nonce = body.nonce.get_secret_value()
    try:
        claims = visitor_google_credentials.verify_credential(body.credential.get_secret_value(), raw_nonce)
        result = visitor_google_login.login(db, raw_nonce, origin, claims)
    except visitor_google_credentials.GoogleVerificationUnavailable:
        db.rollback()
        raise HTTPException(status_code=503, detail=_("visitor_google_unavailable", locale)) from None
    except (visitor_google_credentials.InvalidGoogleCredential, visitor_google_login.InvalidGoogleLogin):
        db.rollback()
        raise HTTPException(status_code=401, detail=_("visitor_google_invalid", locale)) from None
    except visitor_google_login.GoogleEmailCollision:
        db.rollback()
        raise HTTPException(status_code=409, detail=_("visitor_google_link_required", locale)) from None
    except visitor_google_login.GoogleLoginConflict:
        db.rollback()
        raise HTTPException(status_code=409, detail=_("visitor_google_retry", locale)) from None
    db.commit()
    return VisitorLoginOut(session_token=result.raw_session_token, expires_at=result.expires_at)


def _check_login_limit(email: str, ip: str) -> None:
    try:
        visitor_login_guard.check(email, ip)
    except visitor_login_guard.LoginThrottled as exc:
        raise HTTPException(status_code=429, detail="Too many login attempts",
                            headers={"Retry-After": str(exc.retry_after)}) from None


@router.post("/auth/google/link/nonce", response_model=GoogleNonceOut)
@limiter.limit(LOGIN_LIMIT)
def google_link_nonce(request: Request, body: VisitorGoogleNonceIn,
                      _actor: VisitorSession = Depends(visitor_deps.get_visitor_session),
                      db: Session = Depends(deps.get_db),
                      locale: str = Depends(deps.get_locale)) -> GoogleNonceOut:
    origin = _google_origin(request, locale)
    try:
        issued = visitor_google_link.issue_link_challenge(db, request.headers.get("X-Visitor-Token"), origin)
    except visitor_recent_auth.VisitorReauthenticationRequired:
        db.rollback()
        raise HTTPException(status_code=403, detail=_("visitor_recent_auth_required", locale)) from None
    except visitor_google_link.InvalidGoogleLink:
        db.rollback()
        raise HTTPException(status_code=401, detail=_("visitor_google_invalid", locale)) from None
    expires_in = int((issued.row.expires_at - issued.row.created_at).total_seconds())
    db.commit()
    return GoogleNonceOut(nonce=issued.raw_token, expires_in=expires_in)


@router.post("/auth/google/link", status_code=204, response_class=Response)
@limiter.limit(LOGIN_LIMIT)
def google_link(request: Request, body: VisitorGoogleLinkIn,
                _actor: VisitorSession = Depends(visitor_deps.get_visitor_session),
                db: Session = Depends(deps.get_db), locale: str = Depends(deps.get_locale)) -> Response:
    origin = _google_origin(request, locale)
    nonce = body.nonce.get_secret_value()
    try:
        claims = visitor_google_credentials.verify_credential(body.credential.get_secret_value(), nonce)
        visitor_google_link.link(db, request.headers.get("X-Visitor-Token"), nonce, origin, claims)
    except visitor_recent_auth.VisitorReauthenticationRequired:
        db.rollback()
        raise HTTPException(status_code=403, detail=_("visitor_recent_auth_required", locale)) from None
    except visitor_google_credentials.GoogleVerificationUnavailable:
        db.rollback()
        raise HTTPException(status_code=503, detail=_("visitor_google_unavailable", locale)) from None
    except (visitor_google_credentials.InvalidGoogleCredential, visitor_google_link.InvalidGoogleLink):
        db.rollback()
        raise HTTPException(status_code=401, detail=_("visitor_google_invalid", locale)) from None
    except visitor_google_link.GoogleIdentityLinkConflict:
        db.rollback()
        raise HTTPException(status_code=409, detail=_("visitor_google_identity_in_use", locale)) from None
    db.commit()
    return Response(status_code=204)


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


@router.post("/auth/register/request", status_code=202, response_model=VisitorRegistrationRequestOut)
@limiter.limit(SIGNUP_LIMIT)
def request_registration(request: Request, body: VisitorRegistrationRequestIn,
                         db: Session = Depends(deps.get_db),
                         locale: str = Depends(deps.get_locale)) -> VisitorRegistrationRequestOut:
    try:
        prepared = visitor_registration_mail.prepare(db, body.email, locale)
        db.commit()
        if prepared is not None:
            visitor_registration_mail.capture_after_commit(db, prepared)
    except visitor_registration_mail.RegistrationMailUnavailable:
        db.rollback()
        raise HTTPException(status_code=503, detail=_("visitor_registration_mail_unavailable", locale)) from None
    return VisitorRegistrationRequestOut(message=_("visitor_registration_requested", locale))


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


@router.post("/auth/password/reset/confirm", status_code=204, response_class=Response)
@limiter.limit(PASSWORD_RESET_CONFIRM_LIMIT)
def confirm_password_reset(request: Request, body: VisitorPasswordResetIn,
                           db: Session = Depends(deps.get_db),
                           locale: str = Depends(deps.get_locale)) -> Response:
    try:
        visitor_password_reset.confirm_reset(
            db, body.token.get_secret_value(), body.new_password.get_secret_value())
    except visitor_password_reset.InvalidResetProof:
        db.rollback()
        raise HTTPException(status_code=400, detail=_("visitor_password_reset_invalid", locale)) from None
    except visitor_password_reset.InvalidResetPassword:
        db.rollback()
        raise HTTPException(status_code=422, detail=_("visitor_registration_password_invalid", locale)) from None
    db.commit()
    return Response(status_code=204)


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
