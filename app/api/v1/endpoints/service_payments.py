"""Guest payment status, the sandbox checkout callback and the salon refund desk.

Provider webhooks live in their own namespace and are exempt from the per-IP limiter:
they are authenticated by an HMAC over the raw body, not by a browser session.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core import service_payment_service as payments
from app.api.core.i18n import _
from app.api.core.permissions import can_view_financials, require_business_id
from app.api.core.service_payment_provider import WebhookRejected, get_service_payment_provider
from app.api.middleware.rate_limit import (
    SERVICE_PAYMENT_ACTION_LIMIT,
    SERVICE_PAYMENT_STATUS_LIMIT,
    limiter,
)
from app.db.models.user import User
from app.schemas.service_payment import (
    PublicPaymentStatusOut,
    SandboxPaymentOutcomeIn,
    ServicePaymentOut,
    ServicePaymentRefundIn,
    ServicePaymentRefundOut,
)

router = APIRouter()
staff_router = APIRouter()


def _public_attempt(db: Session, public_ref: str, token: Optional[str], locale: str):
    try:
        return payments.resolve_public_attempt(db, public_ref, token)
    except payments.PaymentNotFound:
        raise HTTPException(status_code=404, detail=_("payment_not_found", locale))


@router.post(
    "/webhooks/sandbox",
    summary="Signed sandbox payment event",
    include_in_schema=False,
)
async def sandbox_webhook(
    request: Request,
    x_service_payment_signature: Optional[str] = Header(
        None, alias="X-Service-Payment-Signature"
    ),
    x_service_payment_merchant: Optional[str] = Header(
        None, alias="X-Service-Payment-Merchant"
    ),
    db: Session = Depends(deps.get_db),
):
    raw = await request.body()
    try:
        result = payments.handle_webhook(
            db, raw, x_service_payment_signature, x_service_payment_merchant
        )
    except payments.PaymentNotFound:
        raise HTTPException(status_code=404, detail="payment_not_found")
    except WebhookRejected as exc:
        raise HTTPException(status_code=400, detail=exc.reason)
    return {
        "received": True,
        "duplicate": result.duplicate,
        "status": result.attempt.status,
    }


@router.get(
    "/{public_ref}",
    response_model=PublicPaymentStatusOut,
    summary="Guest-visible state of one deposit",
)
@limiter.limit(SERVICE_PAYMENT_STATUS_LIMIT)
def get_payment_status(
    request: Request,
    public_ref: str,
    x_payment_token: Optional[str] = Header(None, alias="X-Payment-Token"),
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    attempt = _public_attempt(db, public_ref, x_payment_token, locale)
    return payments.payment_status_out(attempt)


@router.post(
    "/{public_ref}/sandbox",
    response_model=PublicPaymentStatusOut,
    summary="Finish a sandbox checkout (never moves money)",
)
@limiter.limit(SERVICE_PAYMENT_ACTION_LIMIT)
def sandbox_outcome(
    request: Request,
    public_ref: str,
    payload: SandboxPaymentOutcomeIn,
    x_payment_token: Optional[str] = Header(None, alias="X-Payment-Token"),
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    if get_service_payment_provider() is None:
        raise HTTPException(status_code=404, detail=_("payment_not_found", locale))
    try:
        attempt = payments.apply_sandbox_outcome(
            db, public_ref, x_payment_token, payload.outcome
        )
    except payments.PaymentNotFound:
        raise HTTPException(status_code=404, detail=_("payment_not_found", locale))
    except WebhookRejected:
        raise HTTPException(status_code=409, detail=_("payment_not_found", locale))
    return payments.payment_status_out(attempt)


def _finance_business_id(current_user: User, db: Session, locale: str) -> int:
    if not can_view_financials(current_user, db):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    return require_business_id(current_user, db, locale)


@staff_router.get(
    "",
    response_model=list[ServicePaymentOut],
    summary="Deposits and holds of this business",
)
def list_service_payments(
    status: Optional[str] = Query(None, max_length=24),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0, le=1_000_000),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    business_id = _finance_business_id(current_user, db, locale)
    attempts = payments.list_attempts(
        db, business_id, status=status, limit=limit, offset=offset
    )
    return [payments.staff_payment_out(attempt) for attempt in attempts]


@staff_router.post(
    "/{attempt_id}/refunds",
    response_model=ServicePaymentRefundOut,
    summary="Refund a paid deposit by an idempotent key",
)
def refund_service_payment(
    attempt_id: int,
    payload: ServicePaymentRefundIn,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    business_id = _finance_business_id(current_user, db, locale)
    try:
        row = payments.refund(
            db,
            business_id,
            attempt_id,
            payload.refund_key,
            payload.amount,
            payload.reason,
        )
    except payments.RefundError as exc:
        codes = {
            "payment_not_found": 404,
            "refund_unavailable": 409,
            "refund_key_conflict": 409,
            "refund_amount_invalid": 422,
            "refund_provider_unavailable": 503,
        }
        detail_key = "refund_not_found" if exc.reason == "payment_not_found" else exc.reason
        raise HTTPException(
            status_code=codes.get(exc.reason, 400),
            detail=_(detail_key, locale),
        )
    return payments.refund_out(row)
