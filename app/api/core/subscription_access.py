"""Server-side subscription access; account and billing stay reachable after the trial."""
from datetime import timedelta
import calendar
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.core.clock import utcnow
from app.api.core.config import settings
from app.api.core.offer_catalog import get_offer_by_code
from app.api.crud import crud_subscription
from app.db.models.subscription import Subscription
from app.db.models.user import User

TRIAL_VISIT_LIMIT = 300
TRIAL_AI_DAILY_LIMIT = 50
READ_ONLY_DAYS = 3


def _is_live(sub: Subscription, now) -> bool:
    return (sub.status in ("active", "pending_cancellation")
            and not (sub.current_period_end and sub.current_period_end <= now)
            and not (sub.is_trial and sub.trial_end and sub.trial_end <= now))


def product_subscriptions(db: Session, payer_id: int, product: str, *, live_only=True):
    rows = db.query(Subscription).filter(Subscription.user_id == payer_id).order_by(Subscription.id.desc()).all()
    result = []
    now = utcnow()
    for sub in rows:
        offer = get_offer_by_code(sub.offer_code, include_draft=True) if sub.offer_code else None
        row_product = offer.product if offer else "crm"
        if row_product != product:
            continue
        if live_only and not _is_live(sub, now):
            continue
        result.append(sub)
    # A newly created AI subscription must never hide an existing CRM subscription.
    return sorted(result, key=lambda sub: (sub.is_trial, -sub.id))


def get_subscription_access_state(db: Session, user_id: int) -> dict[str, Any]:
    user = db.get(User, user_id)
    payer_id = crud_subscription.subscription_payer_id(db, user) if user else None
    state = dict(crm_mode="blocked", has_access=False, is_trial_expired=False,
                 can_export=False, can_use_ai=False, can_manage_billing=bool(payer_id == user_id),
                 can_mutate_paid_features=False, trial_end=None, read_only_until=None,
                 checkout_available=bool(settings.stripe_api_key and not settings.pilot_mode_enabled),
                 pilot_mode=settings.pilot_mode_enabled, reason="no_subscription", ai_daily_limit=None)
    if payer_id is None:
        state["reason"] = "no_payer"
        return state
    history = product_subscriptions(db, payer_id, "crm", live_only=False)
    live = [sub for sub in history if _is_live(sub, utcnow())]
    sub = live[0] if live else (history[0] if history else None)
    if sub is None:
        return state
    state["trial_end"] = sub.trial_end if sub.is_trial else None
    expiry = sub.trial_end if sub.is_trial and sub.trial_end else sub.current_period_end
    if live:
        state.update(crm_mode="full", has_access=True, can_export=True,
                     can_mutate_paid_features=True, reason=None)
        ai_subs = product_subscriptions(db, payer_id, "ai")
        if sub.is_trial:
            state.update(can_use_ai=True, ai_daily_limit=TRIAL_AI_DAILY_LIMIT)
        elif ai_subs:
            ai_offer = get_offer_by_code(ai_subs[0].offer_code)
            if ai_offer:
                state.update(can_use_ai=True, ai_daily_limit=ai_offer.entitlements.daily_requests_limit)
        elif sub.offer_code:
            # The introductory AI month is a grant, never an automatically paid AI subscription.
            paid_history = [row for row in history if not row.is_trial]
            first_paid = min((row.created_at for row in paid_history), default=sub.created_at)
            year, month = (first_paid.year + 1, 1) if first_paid.month == 12 else (first_paid.year, first_paid.month + 1)
            free_until = first_paid.replace(year=year, month=month, day=min(first_paid.day, calendar.monthrange(year, month)[1]))
            if utcnow() < free_until:
                crm_offer = get_offer_by_code(sub.offer_code)
                matching_ai = get_offer_by_code(sub.offer_code.replace("crm_standard", "ai_pro").replace("crm_", "ai_"))
                if crm_offer and matching_ai:
                    state.update(can_use_ai=True, ai_daily_limit=matching_ai.entitlements.daily_requests_limit)
        elif not settings.pilot_mode_enabled:
            # Legacy paid plans keep their existing monthly assistant allowance.
            state["can_use_ai"] = True
        return state
    if expiry is not None:
        state["is_trial_expired"] = bool(sub.is_trial)
        state["reason"] = "trial_expired" if sub.is_trial else "subscription_expired"
        state["read_only_until"] = expiry + timedelta(days=READ_ONLY_DAYS)
        if sub.is_trial and utcnow() < state["read_only_until"]:
            state["crm_mode"] = "read_only"
    return state


def _deny(reason="subscription_required"):
    raise HTTPException(status_code=402, detail={"code": reason})


def enforce_subscription_request_access(db: Session, user: User, request) -> None:
    if user.role not in {"admin", "business_owner", "specialist", "employee"}:
        return
    path = request.url.path.rstrip("/")
    method = request.method
    # Keep sign-in, account security, and billing available without exposing CRM data.
    if path.startswith("/auth/google/") or path.startswith("/subscriptions") or path in {"/users/me", "/users/logout", "/users/logout-all", "/users/change-password"} or path.startswith("/users/2fa"):
        return
    state = get_subscription_access_state(db, user.id)
    if state["reason"] in {"no_subscription", "no_payer"}:
        # No workspace entitlement (for example, after firing) is a scope denial,
        # rather than an expired period that the former staff member can renew.
        raise HTTPException(status_code=403, detail={"code": state["reason"]})
    if state["crm_mode"] == "full":
        if path.startswith("/assistant") and method not in {"GET", "HEAD", "OPTIONS"} and not state["can_use_ai"]:
            _deny("ai_subscription_required")
        return
    segments = path.lower().split("/")
    download = any(segment in {"export", "download", "attachments", "files", "qr"} or segment.endswith((".csv", ".xlsx", ".pdf")) for segment in segments)
    if state["crm_mode"] == "read_only" and method in {"GET", "HEAD", "OPTIONS"} and not download:
        return
    _deny(state["reason"])


def enforce_subscription_mutation_access(db: Session, user_id: int, feature: Optional[str] = None) -> None:
    if get_subscription_access_state(db, user_id)["crm_mode"] != "full":
        _deny()


def enforce_business_access(db: Session, business_id: Optional[int], *, write: bool = True) -> None:
    """Opaque visitor credentials do not bypass the salon's access period."""
    # Without a salon the owner filter would become IS NULL and match every solo owner.
    if business_id is None:
        _deny()
    owners = db.query(User).filter(User.business_id == business_id, User.deleted_at.is_(None), User.role.in_(["business_owner", "admin"])).all()
    for owner in owners:
        state = get_subscription_access_state(db, owner.id)
        if state["crm_mode"] == "full" or (not write and state["crm_mode"] == "read_only"):
            return
    _deny()


def enforce_owner_access(db: Session, owner_id: Optional[int], *, write: bool = True) -> None:
    """Gate a record owned by one user, salon member or solo specialist."""
    owner = db.get(User, owner_id) if owner_id else None
    if owner is None or owner.deleted_at is not None:
        _deny()
    if owner.business_id is not None:
        enforce_business_access(db, owner.business_id, write=write)
        return
    state = get_subscription_access_state(db, owner.id)
    if not (state["crm_mode"] == "full" or (not write and state["crm_mode"] == "read_only")):
        _deny(state["reason"] or "subscription_required")


def enforce_client_access(db: Session, client, *, write: bool = True) -> None:
    """A client's history spans every salon and solo owner holding it; each must be open."""
    from app.db.models.client_business_relation import ClientBusinessRelation
    business_ids = {row.business_id for row in db.query(ClientBusinessRelation.business_id).filter(ClientBusinessRelation.client_id == client.id)}
    if not business_ids and not client.owner_id:
        _deny()
    for business_id in business_ids:
        enforce_business_access(db, business_id, write=write)
    if client.owner_id:
        enforce_owner_access(db, client.owner_id, write=write)
