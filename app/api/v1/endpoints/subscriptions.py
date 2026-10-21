import hmac
from fastapi import APIRouter, Depends, HTTPException, Request, Header
from sqlalchemy.orm import Session
from datetime import datetime
from decimal import Decimal
from app.api.core import stripe_billing, stripe_checkout
import stripe
from app.api import deps
from app.api.core.i18n import _
from app.api.core.config import settings
from app.api.core.logger import logger
from app.api.crud import crud_subscription
from app.db.models.subscription import Subscription
from app.db.models.user import User
from app.schemas.subscription import (
    SubscriptionCreate,
    SubscriptionOut,
    SubscriptionUpdate,
    SubscriptionCancelRequest,
    SubscriptionReactivateRequest,
)
from app.schemas.transaction import TransactionOut
from app.schemas.subscription_plan import (
    SubscriptionPlanCreate,
    SubscriptionPlanOut,
    SubscriptionPlanUpdate,
)
from app.schemas.offer_catalog import (
    EntitlementsUsageOut,
    CrmUsageOut,
    AiUsageOut,
)
router = APIRouter()
_billing_roles = deps.role_required("admin", "business_owner", "specialist")


def _payer(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(_billing_roles),
    locale: str = Depends(deps.get_locale),
):
    # the owner pays for the whole team, staff ride on that and never buy a plan themselves
    from app.api.core.permissions import get_staff_record
    if current_user.employer_id or get_staff_record(db, current_user):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    return current_user


def _own_subscription(db: Session, current_user: User, subscription_id: int, locale: str):
    sub = crud_subscription.get_subscription(db, subscription_id)
    if not sub:
        raise HTTPException(status_code=404, detail=_("subscription_not_found", locale))
    if sub.user_id != current_user.id:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    return sub
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
    return result.model_dump() if hasattr(result, 'model_dump') else result
@router.get("/plans", response_model=list[SubscriptionPlanOut])
def list_plans(
    active_only: bool = True,
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    plans = crud_subscription.list_plans(db, active_only=active_only)
    return [p.model_dump() if hasattr(p, 'model_dump') else p for p in plans]
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
    return result.model_dump() if hasattr(result, 'model_dump') else result
@router.post("/", response_model=SubscriptionOut)
def create_subscription(
    payload: SubscriptionCreate,
    db: Session = Depends(deps.get_db),
    # admin only: everyone else pays through checkout. Specialists used to be able to
    # hand themselves (or anyone) an active plan for free with a non-stripe provider
    _: User = Depends(deps.role_required("admin")),
    locale: str = Depends(deps.get_locale),
):
    if payload.provider == "stripe":
        raise HTTPException(
            status_code=403,
            detail=_("stripe_subscriptions_must_be_created_via_payment", locale)
        )
    plan = crud_subscription.get_plan(db, payload.plan_id)
    if not plan or not plan.is_active:
        raise HTTPException(status_code=400, detail=_("plan_not_active", locale))
    result = crud_subscription.create_subscription(db, payload)
    return result.model_dump() if hasattr(result, 'model_dump') else result
@router.get("/me", response_model=SubscriptionOut | None)
def my_subscription(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(_billing_roles),
    locale: str = Depends(deps.get_locale),
):
    sub = crud_subscription.get_active_subscription(db, current_user.id)
    if sub:
        return sub.model_dump() if hasattr(sub, 'model_dump') else sub
@router.post("/checkout/create-session")
def create_checkout_session(
    plan_id: int | None = None,
    offer_code: str | None = None,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(_payer),
    locale: str = Depends(deps.get_locale),
):
    if not settings.stripe_api_key:
        raise HTTPException(status_code=503, detail="Stripe not configured")
    if not plan_id and not offer_code:
        raise HTTPException(status_code=400, detail="Must provide either plan_id or offer_code")

    try:
        if offer_code:
            from app.api.core.entitlements_service import (
                build_checkout_payload_for_offer,
                resolve_offer_for_checkout,
            )
            from app.api.core.offer_catalog import OFFER_PLAN_CODES
            offer = resolve_offer_for_checkout(offer_code)
            plan = crud_subscription.get_plan_by_code(db, OFFER_PLAN_CODES[offer.code])
            if not plan or not plan.is_active or plan.is_trial or Decimal(str(plan.price)) <= 0:
                raise HTTPException(status_code=400, detail="Offer plan is not available")
            payload = build_checkout_payload_for_offer(current_user, offer, settings.frontend_url, plan.id)
            return stripe_checkout.create_session(db, current_user, plan.id, payload)

        plan = crud_subscription.get_plan(db, plan_id)
        if not plan or not plan.is_active:
            raise HTTPException(status_code=404, detail=_("plan_not_found", locale))
        amount = Decimal(str(plan.price))
        if plan.is_trial or not amount.is_finite() or amount < Decimal("0.01"):
            raise HTTPException(status_code=400, detail="Choose a paid plan for checkout")

        payload = dict(
            line_items=[{
                "price_data": {
                    "currency": plan.currency.lower(),
                    "product_data": {
                        "name": plan.name,
                        "description": plan.features or "",
                    },
                    "unit_amount": int((amount * 100).quantize(Decimal("1"))),
                    "recurring": {
                        "interval": "month" if plan.period_days == 30 else "year" if plan.period_days >= 365 else "month",
                    },
                },
                "quantity": 1,
            }],
            mode="subscription",
            success_url=f"{settings.frontend_url}/subscription?success=true&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{settings.frontend_url}/subscription?canceled=true",
            metadata={"user_id": str(current_user.id), "plan_id": str(plan_id)},
            subscription_data={"metadata": {"user_id": str(current_user.id), "plan_id": str(plan_id)}},
        )
        return stripe_checkout.create_session(db, current_user, plan_id, payload)
    except HTTPException:
        raise
    except stripe.StripeError:
        raise HTTPException(status_code=503, detail="Stripe is temporarily unavailable")
    except Exception as e:
        logger.error(f"Error creating checkout session: {e}")
        raise HTTPException(status_code=500, detail="Failed to create checkout session")
@router.get("/checkout/session/{session_id}")
def get_checkout_session(
    session_id: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(_billing_roles),
    locale: str = Depends(deps.get_locale),
):
    if not settings.stripe_api_key:
        raise HTTPException(status_code=503, detail="Stripe not configured")
    stripe.api_key = settings.stripe_api_key
    try:
        session = stripe.checkout.Session.retrieve(session_id)
        if stripe_billing.as_dict(session.metadata).get("user_id") != str(current_user.id):
            raise HTTPException(status_code=403, detail="Not allowed")
        return {
            "id": session.id,
            "status": session.payment_status,
            "subscription_id": session.subscription,
        }
    except stripe.StripeError:
        raise HTTPException(status_code=503, detail="Stripe is temporarily unavailable")
    return None
@router.get("/trial/status")
def get_trial_status(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    from app.api.crud import crud_subscription
    user_id = crud_subscription.subscription_payer_id(db, current_user)
    # fired staff have nobody paying for them
    is_active = crud_subscription.is_trial_active(db, user_id) if user_id else False
    sub = crud_subscription.get_active_subscription(db, user_id) if user_id else None
    return {
        "is_trial_active": is_active,
        "is_trial": sub.is_trial if sub else False,
        "trial_start": sub.trial_start if sub else None,
        "trial_end": sub.trial_end if sub else None,
        "has_access": crud_subscription.check_subscription_access(db, current_user.id)[0],
    }


@router.get("/usage", response_model=EntitlementsUsageOut)
def get_usage_and_entitlements(
    period: str | None = None,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    """Authenticated endpoint returning CRM and AI usage units and limits.

    Ensures employee cannot view payment credentials or billing details of another business,
    strictly validates period bounds, and reflects payer entitlements.
    """
    import re
    from app.api.core.clock import utcnow
    from app.api.core.crm_metering import count_distinct_active_clients, get_current_period
    from app.api.core.ai_metering import get_daily_ai_usage, get_current_daily_period
    from app.api.core.offer_catalog import get_offer_by_code
    from app.api.core.permissions import get_staff_record

    period_str = period or get_current_period()
    if not re.match(r"^\d{4}-(0[1-9]|1[0-2])$", period_str):
        raise HTTPException(status_code=400, detail="Invalid period format (expected YYYY-MM)")
    year = int(period_str.split("-")[0])
    if not (1900 <= year <= 2100):
        raise HTTPException(status_code=400, detail="Period year out of range")

    payer_id = crud_subscription.subscription_payer_id(db, current_user)
    payer = db.query(User).filter(User.id == payer_id).first() if payer_id else current_user

    is_staff = bool(current_user.employer_id or get_staff_record(db, current_user))
    can_manage_billing = not is_staff
    is_payer = (current_user.id == payer_id) and not is_staff

    biz_id = payer.business_id if payer else None
    usr_id = payer.id if (payer and not biz_id) else None

    # Calculate active clients
    active_clients = count_distinct_active_clients(
        db,
        business_id=biz_id,
        period=period_str,
        user_id=usr_id,
    )

    # Limits come from the offer that was actually paid for, never from the current client
    # count or a hardcoded starter tier. No paid offer means no limit to report.
    sub = crud_subscription.get_active_subscription(db, payer_id) if payer_id else None
    if sub and sub.current_period_end and sub.current_period_end <= utcnow():
        sub = None
    offer = get_offer_by_code(sub.offer_code, include_draft=True) if sub and sub.offer_code else None
    crm_offer = offer if offer and offer.product == "crm" else None
    ai_offer = offer if offer and offer.product == "ai" else None
    crm_limit = crm_offer.entitlements.max_active_clients if crm_offer else None
    crm_code = crm_offer.code if crm_offer else None
    crm_within = (active_clients <= crm_limit) if crm_limit is not None else True

    daily_period = get_current_daily_period()
    ai_used = get_daily_ai_usage(
        db,
        business_id=biz_id,
        user_id=usr_id,
        day_str=daily_period,
    )
    ai_limit = ai_offer.entitlements.daily_requests_limit if ai_offer else None
    ai_code = ai_offer.code if ai_offer else None
    ai_within = (ai_used <= ai_limit) if ai_limit is not None else True

    return EntitlementsUsageOut(
        period=period_str,
        daily_period=daily_period,
        is_payer=is_payer,
        can_manage_billing=can_manage_billing,
        crm=CrmUsageOut(
            active_clients=active_clients,
            limit=crm_limit,
            offer_code=crm_code,
            is_within_limit=crm_within,
        ),
        ai=AiUsageOut(
            daily_requests_used=ai_used,
            daily_requests_limit=ai_limit,
            offer_code=ai_code,
            is_within_limit=ai_within,
        ),
    )
@router.post("/webhooks/stripe")
async def stripe_webhook(request: Request, db: Session = Depends(deps.get_db)):
    if not settings.stripe_webhook_secret:
        raise HTTPException(status_code=503, detail="Stripe webhook secret not configured")
    payload = await request.body()
    try:
        event = stripe.Webhook.construct_event(payload, request.headers.get("Stripe-Signature", ""),
                                               settings.stripe_webhook_secret)
    except (ValueError, stripe.SignatureVerificationError):
        raise HTTPException(status_code=400, detail="Invalid stripe signature")
    try:
        stripe_billing.handle_event(db, event)
        db.commit()
    except stripe.StripeError:
        db.rollback()
        raise HTTPException(status_code=503, detail="Stripe is temporarily unavailable")
    except Exception:
        db.rollback()
        raise
    return {"received": True}


def _check_webhook_token(expected: str, given: str, provider: str) -> None:
    # fail closed: an unset token used to mean "accept anything from anyone"
    if not expected:
        raise HTTPException(status_code=503, detail=f"{provider} webhook is not configured")
    if not hmac.compare_digest(expected, given or ""):
        raise HTTPException(status_code=400, detail=f"Invalid {provider} token")
@router.post("/webhooks/apple")
async def apple_webhook(
    request: Request,
    db: Session = Depends(deps.get_db),
    token: str = Header(default="", alias="X-Apple-Token"),
):
    from app.api.crud import crud_transaction
    from app.schemas.transaction import TransactionCreate
    _check_webhook_token(settings.apple_webhook_token, token, "apple")
    payload = await request.json()
    event_type = payload.get("notification_type", "")
    external_id = str(payload.get("transaction_id") or "")
    status = payload.get("status") or "active"
    sub = crud_subscription.get_by_external(db, "apple", external_id)
    if sub:
        update_data = SubscriptionUpdate(status=status)
        if event_type == "DID_RENEW":
            amount = payload.get("price", 0)
            currency = payload.get("currency", "eur")
            try:
                transaction_data = TransactionCreate(
                    subscription_id=sub.id,
                    amount=amount,
                    currency=currency,
                    payment_method="apple_pay",
                    status="succeeded",
                    description="Subscription renewed via Apple Pay",
                    provider="apple",
                    external_transaction_id=external_id,
                )
                crud_transaction.create_transaction(db, transaction_data)
            except Exception as e:
                logger.warning(f"Failed to log Apple Pay transaction: {e}")
        elif event_type == "DID_FAIL_TO_RENEW":
            try:
                transaction_data = TransactionCreate(
                    subscription_id=sub.id,
                    amount=0.0,
                    currency="eur",
                    payment_method="apple_pay",
                    status="failed",
                    description="Subscription renewal failed via Apple Pay",
                    provider="apple",
                    external_transaction_id=external_id,
                )
                crud_transaction.create_transaction(db, transaction_data)
            except Exception as e:
                logger.warning(f"Failed to log failed Apple Pay transaction: {e}")
        elif event_type == "DID_CANCEL":
            update_data.cancel_at_period_end = True
            update_data.status = "pending_cancellation"
        crud_subscription.update_subscription(db, sub, update_data)
    return {"received": True}
@router.post("/webhooks/google")
async def google_webhook(
    request: Request,
    db: Session = Depends(deps.get_db),
    token: str = Header(default="", alias="X-Google-Token"),
):
    from app.api.crud import crud_transaction
    from app.schemas.transaction import TransactionCreate
    _check_webhook_token(settings.google_webhook_token, token, "google")
    payload = await request.json()
    notification_type = payload.get("notificationType", 0)  
    purchase_token = str(payload.get("purchaseToken") or "")
    subscription_id = str(payload.get("subscriptionId") or purchase_token)
    status = payload.get("status") or "active"
    sub = crud_subscription.get_by_external(db, "google", subscription_id)
    if sub:
        update_data = SubscriptionUpdate(status=status)
        if notification_type == 1:  
            try:
                transaction_data = TransactionCreate(
                    subscription_id=sub.id,
                    amount=sub.plan.price if sub.plan else 0.0,
                    currency=sub.plan.currency if sub.plan else "eur",
                    payment_method="google_pay",
                    status="succeeded",
                    description="Subscription renewed via Google Pay",
                    provider="google",
                    external_transaction_id=purchase_token,
                )
                crud_transaction.create_transaction(db, transaction_data)
            except Exception as e:
                logger.warning(f"Failed to log Google Pay transaction: {e}")
        elif notification_type == 2:  
            update_data.cancel_at_period_end = True
            update_data.status = "pending_cancellation"
        crud_subscription.update_subscription(db, sub, update_data)
    return {"received": True}
@router.post("/webhooks/erl")
async def erl_webhook(
    request: Request,
    db: Session = Depends(deps.get_db),
    token: str = Header(default="", alias="X-ERL-Token"),
):
    from app.api.crud import crud_transaction
    from app.schemas.transaction import TransactionCreate
    _check_webhook_token(settings.erl_webhook_token, token, "erl")
    payload = await request.json()
    event_type = payload.get("event_type", "")
    transaction_id = str(payload.get("transaction_id") or "")
    subscription_id = str(payload.get("subscription_id") or transaction_id)
    status = payload.get("status", "active")
    amount = payload.get("amount", 0.0)
    currency = payload.get("currency", "eur")
    sub = crud_subscription.get_by_external(db, "erl", subscription_id)
    if sub:
        update_data = SubscriptionUpdate(status=status)
        if event_type == "payment.succeeded":
            try:
                transaction_data = TransactionCreate(
                    subscription_id=sub.id,
                    amount=amount,
                    currency=currency,
                    payment_method="erl_pay",
                    status="succeeded",
                    description="Payment succeeded via ERL Pay",
                    provider="erl",
                    external_transaction_id=transaction_id,
                )
                crud_transaction.create_transaction(db, transaction_data)
            except Exception as e:
                logger.warning(f"Failed to log ERL Pay transaction: {e}")
        elif event_type == "payment.failed":
            try:
                transaction_data = TransactionCreate(
                    subscription_id=sub.id,
                    amount=amount,
                    currency=currency,
                    payment_method="erl_pay",
                    status="failed",
                    description="Payment failed via ERL Pay",
                    provider="erl",
                    external_transaction_id=transaction_id,
                )
                crud_transaction.create_transaction(db, transaction_data)
            except Exception as e:
                logger.warning(f"Failed to log failed ERL Pay transaction: {e}")
        elif event_type == "subscription.cancelled":
            update_data.cancel_at_period_end = True
            update_data.status = "pending_cancellation"
        crud_subscription.update_subscription(db, sub, update_data)
    return {"received": True}
@router.post("/{subscription_id}/cancel", response_model=SubscriptionOut)
def cancel_subscription(
    subscription_id: int,
    cancel_request: SubscriptionCancelRequest,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(_billing_roles),
    locale: str = Depends(deps.get_locale),
):
    sub = _own_subscription(db, current_user, subscription_id, locale)
    if sub.provider == "stripe":
        try:
            return stripe_billing.cancel(db, sub, at_period_end=cancel_request.cancel_at_period_end)
        except stripe.StripeError:
            db.rollback()
            raise HTTPException(status_code=503, detail="Stripe is temporarily unavailable")
    cancelled_sub = crud_subscription.cancel_subscription(
        db, 
        subscription_id, 
        cancel_at_period_end=cancel_request.cancel_at_period_end,
        user_id=current_user.id
    )
    if not cancelled_sub:
        raise HTTPException(status_code=400, detail=_("cannot_cancel_subscription", locale))
    return cancelled_sub.model_dump() if hasattr(cancelled_sub, 'model_dump') else cancelled_sub
@router.post("/{subscription_id}/reactivate", response_model=SubscriptionOut)
def reactivate_subscription(
    subscription_id: int,
    reactivate_request: SubscriptionReactivateRequest,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(_payer),
    locale: str = Depends(deps.get_locale),
):
    sub = _own_subscription(db, current_user, subscription_id, locale)
    if sub.provider == "stripe":
        try:
            return stripe_billing.reactivate(db, sub, plan_id=reactivate_request.plan_id)
        except stripe.StripeError:
            db.rollback()
            raise HTTPException(status_code=503, detail="Stripe is temporarily unavailable")
    stripe_billing.validate_reactivation(sub, plan_id=reactivate_request.plan_id)
    reactivated_sub = crud_subscription.reactivate_subscription(
        db,
        subscription_id,
        user_id=current_user.id
    )
    if not reactivated_sub:
        raise HTTPException(status_code=400, detail=_("cannot_reactivate_subscription", locale))
    return reactivated_sub.model_dump() if hasattr(reactivated_sub, 'model_dump') else reactivated_sub
@router.get("/{subscription_id}/transactions", response_model=list[TransactionOut])
def get_subscription_transactions(
    subscription_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(_billing_roles),
    locale: str = Depends(deps.get_locale),
):
    sub = _own_subscription(db, current_user, subscription_id, locale)
    from app.api.crud import crud_transaction
    transactions = crud_transaction.get_transactions_by_subscription(db, subscription_id)
    return [t.model_dump() if hasattr(t, 'model_dump') else t for t in transactions]
@router.get("/history", response_model=list[SubscriptionOut])
def get_subscription_history(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(_billing_roles),
    locale: str = Depends(deps.get_locale),
):
    subscriptions = (
        db.query(Subscription)
        .filter(Subscription.user_id == current_user.id)
        .order_by(Subscription.created_at.desc())
        .all()
    )
    return [s.model_dump() if hasattr(s, 'model_dump') else s for s in subscriptions]
