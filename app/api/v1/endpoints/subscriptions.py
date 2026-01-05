from fastapi import APIRouter, Depends, HTTPException, Request, Header
from sqlalchemy.orm import Session
import stripe

from app.api import deps
from app.api.core.i18n import _, localize_response
from app.api.core.config import settings
from app.api.crud import crud_subscription
from app.db.models.subscription_plan import SubscriptionPlan
from app.db.models.subscription import Subscription
from app.db.models.user import User
from app.schemas.subscription import (
    SubscriptionCreate,
    SubscriptionOut,
    SubscriptionUpdate,
)
from app.schemas.subscription_plan import (
    SubscriptionPlanCreate,
    SubscriptionPlanOut,
    SubscriptionPlanUpdate,
)

router = APIRouter()


@router.post("/plans", response_model=SubscriptionPlanOut)
def create_plan(
    payload: SubscriptionPlanCreate,
    db: Session = Depends(deps.get_db),
    _: User = Depends(deps.role_required("admin")),
    locale: str = Depends(deps.get_locale),
):
    existing = crud_subscription.get_plan_by_code(db, payload.code)
    if existing:
        raise HTTPException(status_code=400, detail=_("plan_not_found", locale))
    result = crud_subscription.create_plan(db, payload)
    return localize_response(result.dict() if hasattr(result, 'dict') else result, locale)


@router.get("/plans", response_model=list[SubscriptionPlanOut])
def list_plans(
    active_only: bool = True,
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    plans = crud_subscription.list_plans(db, active_only=active_only)
    return [localize_response(p.dict() if hasattr(p, 'dict') else p, locale) for p in plans]


@router.put("/plans/{plan_id}", response_model=SubscriptionPlanOut)
def update_plan(
    plan_id: int,
    payload: SubscriptionPlanUpdate,
    db: Session = Depends(deps.get_db),
    _: User = Depends(deps.role_required("admin")),
    locale: str = Depends(deps.get_locale),
):
    plan = crud_subscription.get_plan(db, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail=_("plan_not_found", locale))
    result = crud_subscription.update_plan(db, plan, payload)
    return localize_response(result.dict() if hasattr(result, 'dict') else result, locale)


@router.post("/", response_model=SubscriptionOut)
def create_subscription(
    payload: SubscriptionCreate,
    db: Session = Depends(deps.get_db),
    _: User = Depends(deps.role_required("admin", "specialist")),
    locale: str = Depends(deps.get_locale),
):
    plan = crud_subscription.get_plan(db, payload.plan_id)
    if not plan or not plan.is_active:
        raise HTTPException(status_code=400, detail=_("plan_not_active", locale))
    result = crud_subscription.create_subscription(db, payload)
    return localize_response(result.dict() if hasattr(result, 'dict') else result, locale)


@router.get("/me", response_model=SubscriptionOut | None)
def my_subscription(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
    locale: str = Depends(deps.get_locale),
):
    """Получить текущую активную подписку пользователя."""
    sub = crud_subscription.get_active_subscription(db, current_user.id)
    if sub:
        return localize_response(sub.dict() if hasattr(sub, 'dict') else sub, locale)
    return None


@router.get("/trial/status")
def get_trial_status(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    """Получить статус trial периода для текущего пользователя."""
    from app.api.crud import crud_subscription
    
    # Сотрудники используют подписку админа
    user_id = current_user.employer_id if current_user.employer_id else current_user.id
    
    is_active = crud_subscription.is_trial_active(db, user_id)
    sub = crud_subscription.get_active_subscription(db, user_id)
    
    return {
        "is_trial_active": is_active,
        "is_trial": sub.is_trial if sub else False,
        "trial_start": sub.trial_start if sub else None,
        "trial_end": sub.trial_end if sub else None,
        "has_access": True if sub and sub.status == "active" else False,
    }


@router.post("/webhooks/stripe")
async def stripe_webhook(request: Request, db: Session = Depends(deps.get_db)):
    payload = await request.body()
    sig_header = request.headers.get("Stripe-Signature", "")
    secret = settings.stripe_webhook_secret
    if not secret:
        raise HTTPException(status_code=400, detail="Stripe webhook secret not configured")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, secret)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid stripe signature")
    obj = event["data"]["object"] if isinstance(event, dict) else getattr(event, "data", {}).object
    external_id = str(obj.get("id") or "")
    status = obj.get("status") or "active"
    sub = crud_subscription.get_by_external(db, "stripe", external_id)
    if sub:
        crud_subscription.update_subscription(db, sub, SubscriptionUpdate(status=status))
    return {"received": True}


@router.post("/webhooks/apple")
async def apple_webhook(
    request: Request,
    db: Session = Depends(deps.get_db),
    token: str = Header(default="", alias="X-Apple-Token"),
):
    if settings.apple_webhook_token and token != settings.apple_webhook_token:
        raise HTTPException(status_code=400, detail="Invalid apple token")
    payload = await request.json()
    external_id = str(payload.get("transaction_id") or "")
    status = payload.get("status") or "active"
    sub = crud_subscription.get_by_external(db, "apple", external_id)
    if sub:
        crud_subscription.update_subscription(db, sub, SubscriptionUpdate(status=status))
    return {"received": True}


@router.post("/webhooks/google")
async def google_webhook(
    request: Request,
    db: Session = Depends(deps.get_db),
    token: str = Header(default="", alias="X-Google-Token"),
):
    if settings.google_webhook_token and token != settings.google_webhook_token:
        raise HTTPException(status_code=400, detail="Invalid google token")
    payload = await request.json()
    external_id = str(payload.get("purchase_token") or "")
    status = payload.get("status") or "active"
    sub = crud_subscription.get_by_external(db, "google", external_id)
    if sub:
        crud_subscription.update_subscription(db, sub, SubscriptionUpdate(status=status))
    return {"received": True}

