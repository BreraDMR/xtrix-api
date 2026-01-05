from fastapi import APIRouter, Depends, HTTPException, Request, Header
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
import stripe

from app.api import deps
from app.api.core.i18n import _, localize_response
from app.api.core.config import settings
from app.api.core.logger import logger
from app.api.crud import crud_subscription
from app.db.models.subscription_plan import SubscriptionPlan
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


@router.post("/checkout/create-session")
def create_checkout_session(
    plan_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
    locale: str = Depends(deps.get_locale),
):
    """Создать Stripe Checkout сессию для оплаты подписки."""
    if not settings.stripe_api_key:
        raise HTTPException(status_code=500, detail="Stripe not configured")
    
    plan = crud_subscription.get_plan(db, plan_id)
    if not plan or not plan.is_active:
        raise HTTPException(status_code=404, detail=_("plan_not_found", locale) or "Plan not found")
    
    stripe.api_key = settings.stripe_api_key
    
    try:
        # Создаем или получаем Stripe customer
        customer_email = current_user.email
        if not customer_email:
            raise HTTPException(status_code=400, detail="User email is required for payment")
        
        # Ищем существующего customer или создаем нового
        customers = stripe.Customer.list(email=customer_email, limit=1)
        if customers.data:
            customer = customers.data[0]
        else:
            customer = stripe.Customer.create(
                email=customer_email,
                name=current_user.full_name,
                metadata={"user_id": current_user.id}
            )
        
        # Создаем checkout session
        checkout_session = stripe.checkout.Session.create(
            customer=customer.id,
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": plan.currency.lower(),
                    "product_data": {
                        "name": plan.name,
                        "description": plan.features or "",
                    },
                    "unit_amount": int(plan.price * 100),  # Stripe использует центы
                    "recurring": {
                        "interval": "month" if plan.period_days == 30 else "year" if plan.period_days >= 365 else "month",
                    },
                },
                "quantity": 1,
            }],
            mode="subscription",
            success_url=f"{settings.frontend_url}/subscription?success=true&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{settings.frontend_url}/subscription?canceled=true",
            metadata={
                "user_id": current_user.id,
                "plan_id": plan_id,
            },
        )
        
        return {
            "session_id": checkout_session.id,
            "url": checkout_session.url,
        }
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error: {e}")
        raise HTTPException(status_code=400, detail=f"Payment error: {str(e)}")
    except Exception as e:
        logger.error(f"Error creating checkout session: {e}")
        raise HTTPException(status_code=500, detail="Failed to create checkout session")


@router.get("/checkout/session/{session_id}")
def get_checkout_session(
    session_id: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
    locale: str = Depends(deps.get_locale),
):
    """Получить информацию о Stripe Checkout сессии."""
    if not settings.stripe_api_key:
        raise HTTPException(status_code=500, detail="Stripe not configured")
    
    stripe.api_key = settings.stripe_api_key
    
    try:
        session = stripe.checkout.Session.retrieve(session_id)
        
        # Проверяем, что сессия принадлежит текущему пользователю
        if session.metadata.get("user_id") != str(current_user.id):
            raise HTTPException(status_code=403, detail="Not allowed")
        
        if session.payment_status == "paid":
            # Создаем подписку на основе успешной оплаты
            subscription_id = session.subscription
            if subscription_id:
                # Получаем plan_id из metadata
                plan_id = int(session.metadata.get("plan_id", 0))
                if plan_id:
                    plan = crud_subscription.get_plan(db, plan_id)
                    if plan:
                        # Создаем или обновляем подписку
                        existing_sub = crud_subscription.get_by_external(db, "stripe", subscription_id)
                        if not existing_sub:
                            from app.schemas.subscription import SubscriptionCreate
                            sub_data = SubscriptionCreate(
                                user_id=current_user.id,
                                plan_id=plan_id,
                                provider="stripe",
                                external_id=subscription_id,
                                status="active",
                            )
                            crud_subscription.create_subscription(db, sub_data)
                            # Логируем транзакцию
                            from app.api.crud import crud_transaction
                            from app.schemas.transaction import TransactionCreate
                            transaction_data = TransactionCreate(
                                subscription_id=existing_sub.id if existing_sub else 0,
                                amount=plan.price,
                                currency=plan.currency,
                                payment_method="card",
                                status="succeeded",
                                description=f"Subscription payment via Stripe Checkout",
                                provider="stripe",
                                external_transaction_id=session.payment_intent,
                            )
                            crud_transaction.create_transaction(db, transaction_data)
        
        return {
            "id": session.id,
            "status": session.payment_status,
            "subscription_id": session.subscription,
        }
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error: {e}")
        raise HTTPException(status_code=400, detail=f"Stripe error: {str(e)}")
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
    """Обработка webhooks от Stripe."""
    from app.api.crud import crud_transaction
    from app.schemas.transaction import TransactionCreate
    
    payload = await request.body()
    sig_header = request.headers.get("Stripe-Signature", "")
    secret = settings.stripe_webhook_secret
    if not secret:
        raise HTTPException(status_code=400, detail="Stripe webhook secret not configured")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, secret)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid stripe signature")
    
    event_type = event.get("type", "")
    obj = event.get("data", {}).get("object", {})
    
    # Обрабатываем различные события Stripe
    if event_type == "customer.subscription.created":
        external_id = str(obj.get("id", ""))
        status = obj.get("status", "active")
        sub = crud_subscription.get_by_external(db, "stripe", external_id)
        if sub:
            crud_subscription.update_subscription(db, sub, SubscriptionUpdate(status=status))
            # Логируем создание подписки
            try:
                transaction_data = TransactionCreate(
                    subscription_id=sub.id,
                    amount=0.0,
                    currency="eur",
                    payment_method="stripe",
                    status="succeeded",
                    description=f"Subscription created via Stripe",
                    provider="stripe",
                    external_transaction_id=external_id,
                )
                crud_transaction.create_transaction(db, transaction_data)
            except Exception as e:
                logger.warning(f"Failed to log subscription creation: {e}")
    
    elif event_type == "customer.subscription.updated":
        external_id = str(obj.get("id", ""))
        status = obj.get("status", "active")
        cancel_at_period_end = obj.get("cancel_at_period_end", False)
        sub = crud_subscription.get_by_external(db, "stripe", external_id)
        if sub:
            update_data = SubscriptionUpdate(status=status)
            if cancel_at_period_end:
                update_data.cancel_at_period_end = True
                update_data.status = "pending_cancellation" if status == "active" else status
            crud_subscription.update_subscription(db, sub, update_data)
    
    elif event_type == "customer.subscription.deleted":
        external_id = str(obj.get("id", ""))
        sub = crud_subscription.get_by_external(db, "stripe", external_id)
        if sub:
            crud_subscription.update_subscription(db, sub, SubscriptionUpdate(status="cancelled"))
    
    elif event_type == "invoice.payment_succeeded":
        # Успешная оплата
        subscription_id = obj.get("subscription", "")
        amount_paid = obj.get("amount_paid", 0) / 100.0  # Stripe использует центы
        currency = obj.get("currency", "eur")
        invoice_id = str(obj.get("id", ""))
        
        sub = crud_subscription.get_by_external(db, "stripe", subscription_id)
        if sub:
            # Обновляем дату окончания периода
            period_end = obj.get("period_end")
            if period_end:
                from datetime import datetime
                update_data = SubscriptionUpdate(
                    current_period_end=datetime.fromtimestamp(period_end)
                )
                crud_subscription.update_subscription(db, sub, update_data)
            
            # Логируем транзакцию
            try:
                transaction_data = TransactionCreate(
                    subscription_id=sub.id,
                    amount=amount_paid,
                    currency=currency,
                    payment_method="card",
                    status="succeeded",
                    description=f"Payment succeeded via Stripe",
                    provider="stripe",
                    external_transaction_id=invoice_id,
                )
                crud_transaction.create_transaction(db, transaction_data)
            except Exception as e:
                logger.warning(f"Failed to log payment transaction: {e}")
    
    elif event_type == "invoice.payment_failed":
        # Неуспешная оплата
        subscription_id = obj.get("subscription", "")
        amount_due = obj.get("amount_due", 0) / 100.0
        currency = obj.get("currency", "eur")
        invoice_id = str(obj.get("id", ""))
        
        sub = crud_subscription.get_by_external(db, "stripe", subscription_id)
        if sub:
            # Логируем неуспешную транзакцию
            try:
                transaction_data = TransactionCreate(
                    subscription_id=sub.id,
                    amount=amount_due,
                    currency=currency,
                    payment_method="card",
                    status="failed",
                    description=f"Payment failed via Stripe",
                    provider="stripe",
                    external_transaction_id=invoice_id,
                )
                crud_transaction.create_transaction(db, transaction_data)
            except Exception as e:
                logger.warning(f"Failed to log failed payment: {e}")
    
    else:
        # Для других событий просто обновляем статус если есть subscription_id
        external_id = str(obj.get("id", "") or obj.get("subscription", ""))
        if external_id:
            status = obj.get("status", "active")
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
    """Обработка webhooks от Apple Pay."""
    from app.api.crud import crud_transaction
    from app.schemas.transaction import TransactionCreate
    
    if settings.apple_webhook_token and token != settings.apple_webhook_token:
        raise HTTPException(status_code=400, detail="Invalid apple token")
    payload = await request.json()
    event_type = payload.get("notification_type", "")
    external_id = str(payload.get("transaction_id") or "")
    status = payload.get("status") or "active"
    
    sub = crud_subscription.get_by_external(db, "apple", external_id)
    if sub:
        update_data = SubscriptionUpdate(status=status)
        
        # Обрабатываем различные события
        if event_type == "DID_RENEW":
            # Успешное продление подписки
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
            # Неуспешное продление
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
            # Отмена подписки
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
    """Обработка webhooks от Google Pay."""
    from app.api.crud import crud_transaction
    from app.schemas.transaction import TransactionCreate
    
    if settings.google_webhook_token and token != settings.google_webhook_token:
        raise HTTPException(status_code=400, detail="Invalid google token")
    payload = await request.json()
    notification_type = payload.get("notificationType", 0)  # 1 = SUBSCRIPTION_RENEWED, 2 = SUBSCRIPTION_CANCELED, etc.
    purchase_token = str(payload.get("purchaseToken") or "")
    subscription_id = str(payload.get("subscriptionId") or purchase_token)
    status = payload.get("status") or "active"
    
    sub = crud_subscription.get_by_external(db, "google", subscription_id)
    if sub:
        update_data = SubscriptionUpdate(status=status)
        
        # Обрабатываем различные события Google Play
        if notification_type == 1:  # SUBSCRIPTION_RENEWED
            # Успешное продление
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
        
        elif notification_type == 2:  # SUBSCRIPTION_CANCELED
            # Отмена подписки
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
    """Обработка webhooks от ERL Pay."""
    from app.api.crud import crud_transaction
    from app.schemas.transaction import TransactionCreate
    
    # Проверка токена (нужно добавить в config)
    erl_webhook_token = getattr(settings, 'erl_webhook_token', None)
    if erl_webhook_token and token != erl_webhook_token:
        raise HTTPException(status_code=400, detail="Invalid ERL token")
    
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
            # Успешная оплата
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
            # Неуспешная оплата
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
            # Отмена подписки
            update_data.cancel_at_period_end = True
            update_data.status = "pending_cancellation"
        
        crud_subscription.update_subscription(db, sub, update_data)
    return {"received": True}


@router.post("/{subscription_id}/cancel", response_model=SubscriptionOut)
def cancel_subscription(
    subscription_id: int,
    cancel_request: SubscriptionCancelRequest,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
    locale: str = Depends(deps.get_locale),
):
    """Отменить подписку. Доступ остается до конца оплаченного периода."""
    # Проверяем, что подписка принадлежит текущему пользователю
    sub = crud_subscription.get_subscription(db, subscription_id)
    if not sub:
        raise HTTPException(status_code=404, detail=_("subscription_not_found", locale) or "Subscription not found")
    
    if sub.user_id != current_user.id:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale) or "Not allowed")
    
    cancelled_sub = crud_subscription.cancel_subscription(
        db, 
        subscription_id, 
        cancel_at_period_end=cancel_request.cancel_at_period_end,
        user_id=current_user.id
    )
    
    if not cancelled_sub:
        raise HTTPException(status_code=400, detail=_("cannot_cancel_subscription", locale) or "Cannot cancel subscription")
    
    return localize_response(cancelled_sub.dict() if hasattr(cancelled_sub, 'dict') else cancelled_sub, locale)


@router.post("/{subscription_id}/reactivate", response_model=SubscriptionOut)
def reactivate_subscription(
    subscription_id: int,
    reactivate_request: SubscriptionReactivateRequest,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
    locale: str = Depends(deps.get_locale),
):
    """Возобновить отмененную подписку."""
    # Проверяем, что подписка принадлежит текущему пользователю
    sub = crud_subscription.get_subscription(db, subscription_id)
    if not sub:
        raise HTTPException(status_code=404, detail=_("subscription_not_found", locale) or "Subscription not found")
    
    if sub.user_id != current_user.id:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale) or "Not allowed")
    
    reactivated_sub = crud_subscription.reactivate_subscription(
        db,
        subscription_id,
        plan_id=reactivate_request.plan_id,
        user_id=current_user.id
    )
    
    if not reactivated_sub:
        raise HTTPException(status_code=400, detail=_("cannot_reactivate_subscription", locale) or "Cannot reactivate subscription")
    
    return localize_response(reactivated_sub.dict() if hasattr(reactivated_sub, 'dict') else reactivated_sub, locale)


@router.get("/{subscription_id}/transactions", response_model=list[TransactionOut])
def get_subscription_transactions(
    subscription_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
    locale: str = Depends(deps.get_locale),
):
    """Получить историю транзакций для подписки."""
    # Проверяем, что подписка принадлежит текущему пользователю
    sub = crud_subscription.get_subscription(db, subscription_id)
    if not sub:
        raise HTTPException(status_code=404, detail=_("subscription_not_found", locale) or "Subscription not found")
    
    if sub.user_id != current_user.id:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale) or "Not allowed")
    
    from app.api.crud import crud_transaction
    transactions = crud_transaction.get_transactions_by_subscription(db, subscription_id)
    
    return [localize_response(t.dict() if hasattr(t, 'dict') else t, locale) for t in transactions]


@router.get("/history", response_model=list[SubscriptionOut])
def get_subscription_history(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
    locale: str = Depends(deps.get_locale),
):
    """Получить историю всех подписок пользователя."""
    from app.db.models.subscription import Subscription
    
    subscriptions = (
        db.query(Subscription)
        .filter(Subscription.user_id == current_user.id)
        .order_by(Subscription.created_at.desc())
        .all()
    )
    
    return [localize_response(s.dict() if hasattr(s, 'dict') else s, locale) for s in subscriptions]

