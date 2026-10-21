"""Verify Google's identity, then issue our normal revocable session tokens."""
import hashlib
import hmac
import secrets
from datetime import timedelta
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from google.auth import exceptions as google_errors
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.core import login_guard
from app.api.core.clock import utcnow
from app.api.core.config import settings
from app.api.core.login_guard import LoginThrottled
from app.api.core.security import create_access_token, create_refresh_token, verify_password
from app.api.core.subscription_policy import calculate_trial_dates, get_crm_trial_days
from app.api.core.totp import verify_totp_token
from app.api.crud.crud_refresh_token import create_refresh_token as save_refresh_token
from app.api.middleware.rate_limit import client_ip
from app.db.models.google_identity import GoogleIdentity, GoogleLoginNonce
from app.db.models.subscription import Subscription
from app.db.models.subscription_plan import SubscriptionPlan
from app.db.models.user import User
from app.schemas.google_identity import GoogleLoginIn, GoogleNonceOut
from app.schemas.refresh_token import RefreshTokenCreate
from app.schemas.user import LoginResponse

NONCE_SECONDS = 600


def _fail(code: str, status: int = 401):
    raise HTTPException(status_code=status, detail={"code": code})


def _origin(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def _require_origin(request: Request) -> str:
    if not settings.google_client_id.strip():
        _fail("google_login_disabled", 503)
    raw = request.headers.get("origin", "")
    allowed = {_origin(settings.frontend_url), *(_origin(url) for url in settings.get_cors_origins_list())}
    if not raw or not _origin(raw) or raw != _origin(raw) or raw not in allowed:
        _fail("google_origin_not_allowed", 403)
    return raw


def _google_request(url, method="GET", **kwargs):
    # The verifier only fetches Google's published signing certificates, never a URL from the token.
    kwargs["timeout"] = 5
    return GoogleRequest()(url, method=method, **kwargs)


def verify_credential(credential: str) -> dict:
    return id_token.verify_oauth2_token(credential, _google_request, settings.google_client_id.strip())


def issue_nonce(db: Session, request: Request) -> GoogleNonceOut:
    origin = _require_origin(request)
    now = utcnow()
    db.query(GoogleLoginNonce).filter(GoogleLoginNonce.expires_at < now).delete(synchronize_session=False)
    nonce = secrets.token_urlsafe(32)
    db.add(GoogleLoginNonce(
        nonce_hash=hashlib.sha256(nonce.encode()).hexdigest(),
        origin=origin,
        expires_at=now + timedelta(seconds=NONCE_SECONDS),
    ))
    db.commit()
    return GoogleNonceOut(nonce=nonce, expires_in=NONCE_SECONDS)


def _create_user(db: Session, claims: dict, email: str, role: str) -> User:
    if settings.pilot_mode_enabled:
        invited = {address.strip().casefold() for address in settings.pilot_invited_emails.split(",") if address.strip()}
        if email not in invited:
            _fail("pilot_invitation_required", 403)
    # Keep the user, identity and trial in one transaction. The older CRUD helper commits halfway.
    user = User(
        full_name=str(claims.get("name") or email.split("@")[0])[:255],
        email=email, role=role, hashed_password="", moderation_status="approved",
    )
    db.add(user)
    db.flush()
    trial_days = get_crm_trial_days()
    plan_role = "admin" if role == "business_owner" else "specialist"
    code = f"trial_{plan_role}_{trial_days}d"
    db.execute(text("SELECT pg_advisory_xact_lock(7531051)"))
    plan = db.query(SubscriptionPlan).filter_by(code=code).first()
    if plan is None:
        plan = SubscriptionPlan(
            code=code, name=f"Trial {plan_role.title()} ({trial_days} Days)",
            period_days=trial_days, price=0, currency="czk", is_active=True,
            is_trial=True, trial_days=trial_days,
        )
        db.add(plan)
        db.flush()
    start, end = calculate_trial_dates(trial_days)
    db.add(Subscription(
        user_id=user.id, plan_id=plan.id, status="active", current_period_end=end,
        is_trial=True, trial_start=start, trial_end=end,
    ))
    return user


def login(db: Session, request: Request, body: GoogleLoginIn) -> LoginResponse:
    origin = _require_origin(request)
    try:
        claims = verify_credential(body.credential)
    except google_errors.TransportError:
        _fail("google_temporarily_unavailable", 503)
    except (ValueError, google_errors.GoogleAuthError):
        _fail("google_invalid_credential")
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject or len(subject) > 255 or claims.get("email_verified") is not True:
        _fail("google_invalid_credential")
    try:
        email = str(TypeAdapter(EmailStr).validate_python(claims.get("email"))).casefold()
    except ValidationError:
        _fail("google_invalid_credential")
    if not isinstance(claims.get("nonce"), str) or not hmac.compare_digest(claims["nonce"], body.nonce):
        _fail("google_invalid_credential")
    # Row locking makes a challenge single-use even if two login requests arrive together.
    nonce = db.query(GoogleLoginNonce).filter_by(
        nonce_hash=hashlib.sha256(body.nonce.encode()).hexdigest(),
    ).populate_existing().with_for_update().first()
    if nonce is None or nonce.used_at is not None or nonce.expires_at <= utcnow() or nonce.origin != origin:
        _fail("google_nonce_invalid")
    try:
        # Different valid challenges for one Google subject must not create multiple accounts.
        lock_key = int.from_bytes(hashlib.sha256(subject.encode()).digest()[:8], "big", signed=True)
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
        identity = db.query(GoogleIdentity).filter_by(subject=subject).first()
        if identity is not None:
            user = db.query(User).filter_by(id=identity.user_id).populate_existing().with_for_update().one()
        else:
            user = db.query(User).filter(func.lower(User.email) == email).populate_existing().with_for_update().first()
            if user is not None:
                if user.deleted_at is not None:
                    _fail("google_invalid_credential")
                if not body.link_existing:
                    _fail("google_account_link_required", 409)
                login_guard.check(user.id, client_ip(request))
                if not body.password or not verify_password(body.password, user.hashed_password):
                    login_guard.failed(user.id, client_ip(request))
                    _fail("google_invalid_credential")
                if db.query(GoogleIdentity).filter_by(user_id=user.id).first():
                    _fail("google_identity_already_linked", 409)
            else:
                user = _create_user(db, claims, email, body.role)
        if user.deleted_at is not None or user.moderation_status != "approved":
            _fail("google_invalid_credential")
        if user.is_locked and (user.locked_until is None or user.locked_until > utcnow()):
            _fail("account_locked", 423)
        login_guard.check(user.id, client_ip(request))
        if user.is_2fa_enabled:
            if not body.totp_token:
                return LoginResponse(requires_2fa=True)
            if not user.totp_secret or not verify_totp_token(user.totp_secret, body.totp_token):
                login_guard.failed(user.id, client_ip(request))
                _fail("google_invalid_credential")
        if identity is None:
            db.add(GoogleIdentity(subject=subject, user_id=user.id))
        access = create_access_token({"sub": str(user.id), "role": user.role})
        refresh, expires = create_refresh_token({"sub": str(user.id), "role": user.role})
        save_refresh_token(db, RefreshTokenCreate(token=refresh, user_id=user.id, expires_at=expires), commit=False)
        nonce.used_at = utcnow()
        db.commit()
        login_guard.succeeded(user.id, client_ip(request))
        return LoginResponse(access_token=access, refresh_token=refresh)
    except IntegrityError:
        db.rollback()
        _fail("google_identity_already_linked", 409)
    except LoginThrottled as exc:
        raise HTTPException(
            status_code=429, detail={"code": "account_locked"},
            headers={"Retry-After": str(exc.retry_after)},
        ) from None
