"""Gift shop for guests and the certificate desk for the salon.

The public side resolves the salon by the same slug as its online booking link.
The buyer keeps a token that was never put in a URL path, so the order page and the
certificate code are visible only to whoever owns that token. Staff operations are
scoped to their business; a foreign code looks exactly like an unknown one.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core import gift_service as gifts
from app.api.core.i18n import _
from app.api.core.permissions import (
    can_create_visits,
    can_view_financials,
    require_business_id,
)
from app.api.core.service_payment_provider import (
    WebhookRejected,
    get_service_payment_provider,
)
from app.api.middleware.rate_limit import (
    GIFT_ACTION_LIMIT,
    GIFT_PURCHASE_LIMIT,
    GIFT_SHOP_READ_LIMIT,
    limiter,
)
from app.db.models.business import Business
from app.db.models.user import User
from app.schemas.gift import (
    GiftCertificateDetailOut,
    GiftCertificateOut,
    GiftOfferIn,
    GiftOfferOut,
    GiftOrderOut,
    GiftPurchaseIn,
    GiftRedemptionIn,
    GiftRedemptionOut,
    GiftReversalIn,
    GiftReversalOut,
    GiftSandboxOutcomeIn,
    GiftShopOut,
)

router = APIRouter()
offers_router = APIRouter()
certificates_router = APIRouter()

_ERROR_STATUS = {
    "gift_offer_unavailable": 404,
    "gift_code_invalid": 404,
    "gift_redemption_not_found": 404,
    "gift_not_found": 404,
    "gift_visit_invalid": 404,
    "gift_request_conflict": 409,
    "gift_expired": 409,
    "gift_insufficient_balance": 409,
    "gift_scope_mismatch": 409,
    "gift_service_invalid": 422,
}


def _raise_gift(exc: gifts.GiftError, locale: str) -> None:
    raise HTTPException(
        status_code=_ERROR_STATUS.get(exc.reason, 409),
        detail=_(exc.reason, locale),
    )


def _offer_error(exc: gifts.GiftError, locale: str) -> None:
    _raise_gift(exc, locale)


def _financial_business_id(current_user: User, db: Session, locale: str) -> int:
    if not can_view_financials(current_user, db):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    return require_business_id(current_user, db, locale)


def _visits_business_id(current_user: User, db: Session, locale: str) -> int:
    if not can_create_visits(current_user, db):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    return require_business_id(current_user, db, locale)


@router.post(
    "/webhooks/sandbox",
    summary="Signed sandbox gift payment event",
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
    provider = get_service_payment_provider()
    if provider is None:
        raise HTTPException(status_code=404, detail="gift_payment_unavailable")
    raw = await request.body()
    try:
        result = gifts.handle_webhook(
            db, provider, raw, x_service_payment_signature, x_service_payment_merchant
        )
    except gifts.GiftNotFound:
        raise HTTPException(status_code=404, detail="gift_not_found")
    except WebhookRejected as exc:
        raise HTTPException(status_code=400, detail=exc.reason)
    return {
        "received": True,
        "duplicate": result.duplicate,
        "status": result.order.status,
    }


@router.get(
    "/orders/{public_ref}",
    response_model=GiftOrderOut,
    summary="Buyer-visible state of one gift order",
)
@limiter.limit(GIFT_SHOP_READ_LIMIT)
def get_gift_order(
    request: Request,
    public_ref: str,
    x_gift_token: Optional[str] = Header(None, alias="X-Gift-Token"),
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    try:
        order = gifts.resolve_order(db, public_ref, x_gift_token)
    except gifts.GiftNotFound:
        raise HTTPException(status_code=404, detail=_("gift_not_found", locale))
    return gifts.order_out(db, order, reveal_code=order.status == gifts.PAID_ORDER)


@router.post(
    "/orders/{public_ref}/sandbox",
    response_model=GiftOrderOut,
    summary="Finish a sandbox gift checkout (never moves money)",
)
@limiter.limit(GIFT_ACTION_LIMIT)
def gift_sandbox_outcome(
    request: Request,
    public_ref: str,
    payload: GiftSandboxOutcomeIn,
    x_gift_token: Optional[str] = Header(None, alias="X-Gift-Token"),
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    provider = get_service_payment_provider()
    if provider is None:
        raise HTTPException(status_code=404, detail=_("gift_not_found", locale))
    try:
        order = gifts.apply_sandbox_outcome(
            db, provider, public_ref, x_gift_token, payload.outcome
        )
    except gifts.GiftNotFound:
        raise HTTPException(status_code=404, detail=_("gift_not_found", locale))
    except WebhookRejected:
        raise HTTPException(status_code=409, detail=_("gift_not_found", locale))
    return gifts.order_out(db, order, reveal_code=order.status == gifts.PAID_ORDER)


@router.get(
    "/{slug}",
    response_model=GiftShopOut,
    summary="Certificates and visit packages on sale",
)
@limiter.limit(GIFT_SHOP_READ_LIMIT)
def get_gift_shop(
    request: Request,
    slug: str,
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    try:
        business, resolved = gifts.get_shop(db, slug)
    except gifts.GiftNotFound:
        raise HTTPException(status_code=404, detail=_("gift_shop_unavailable", locale))
    return gifts.shop_out(
        db,
        business,
        resolved,
        payments_available=get_service_payment_provider() is not None,
    )


@router.post(
    "/{slug}/orders",
    response_model=GiftOrderOut,
    status_code=201,
    summary="Start a gift purchase with a retry-safe request key",
)
@limiter.limit(GIFT_PURCHASE_LIMIT)
def create_gift_order(
    request: Request,
    slug: str,
    payload: GiftPurchaseIn,
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    provider = get_service_payment_provider()
    if provider is None:
        raise HTTPException(status_code=503, detail=_("gift_payment_unavailable", locale))
    try:
        business, _resolved = gifts.get_shop(db, slug, write=True)
    except gifts.GiftNotFound:
        raise HTTPException(status_code=404, detail=_("gift_shop_unavailable", locale))
    try:
        offer = gifts.published_offer(db, business.id, payload.offer_id)
        order = gifts.create_order(db, business, offer, provider, payload)
    except gifts.GiftUnavailable:
        raise HTTPException(status_code=503, detail=_("gift_payment_unavailable", locale))
    except gifts.GiftError as exc:
        _raise_gift(exc, locale)
    return gifts.order_out(db, order, reveal_code=order.status == gifts.PAID_ORDER)


@offers_router.get(
    "",
    response_model=list[GiftOfferOut],
    summary="Gift offers of this business",
)
def list_gift_offers(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner")),
    locale: str = Depends(deps.get_locale),
):
    business_id = require_business_id(current_user, db, locale)
    return [gifts.offer_out(row) for row in gifts.list_offers(db, business_id)]


@offers_router.post(
    "",
    response_model=GiftOfferOut,
    status_code=201,
    summary="Create a certificate or visit package offer",
)
def create_gift_offer(
    payload: GiftOfferIn,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner")),
    locale: str = Depends(deps.get_locale),
):
    business_id = require_business_id(current_user, db, locale)
    business = db.get(Business, business_id)
    if business is None:
        raise HTTPException(status_code=404, detail=_("business_not_found", locale))
    try:
        offer = gifts.create_offer(db, business, payload)
    except gifts.GiftError as exc:
        _offer_error(exc, locale)
    return gifts.offer_out(offer)


@offers_router.put(
    "/{offer_id}",
    response_model=GiftOfferOut,
    summary="Change a gift offer",
)
def update_gift_offer(
    offer_id: int,
    payload: GiftOfferIn,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner")),
    locale: str = Depends(deps.get_locale),
):
    business_id = require_business_id(current_user, db, locale)
    try:
        offer = gifts.update_offer(db, business_id, offer_id, payload)
    except gifts.GiftNotFound:
        raise HTTPException(status_code=404, detail=_("gift_offer_not_found", locale))
    except gifts.GiftError as exc:
        _offer_error(exc, locale)
    return gifts.offer_out(offer)


@certificates_router.get(
    "",
    response_model=list[GiftCertificateOut],
    summary="Certificates and packages of this business with computed balances",
)
def list_gift_certificates(
    status: Optional[str] = Query(None, max_length=24),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0, le=1_000_000),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    business_id = _financial_business_id(current_user, db, locale)
    rows = gifts.list_certificates(
        db, business_id, status=status, limit=limit, offset=offset
    )
    return [gifts.certificate_out(db, row) for row in rows]


@certificates_router.get(
    "/lookup",
    response_model=GiftCertificateDetailOut,
    summary="Find one certificate by the code the guest presented",
)
def lookup_gift_certificate(
    code: str = Query(..., min_length=4, max_length=40),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    business_id = _visits_business_id(current_user, db, locale)
    try:
        certificate = gifts.lookup_certificate(db, business_id, code)
    except gifts.GiftNotFound:
        raise HTTPException(status_code=404, detail=_("gift_code_invalid", locale))
    return gifts.certificate_detail_out(db, certificate)


@certificates_router.get(
    "/{certificate_id}",
    response_model=GiftCertificateDetailOut,
    summary="One certificate with its ledger",
)
def get_gift_certificate(
    certificate_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    business_id = _financial_business_id(current_user, db, locale)
    try:
        certificate = gifts.get_certificate(db, business_id, certificate_id)
    except gifts.GiftNotFound:
        raise HTTPException(status_code=404, detail=_("gift_not_found", locale))
    return gifts.certificate_detail_out(db, certificate)


@certificates_router.post(
    "/redeem",
    response_model=GiftRedemptionOut,
    summary="Spend part of the balance or one visit with a retry key",
)
def redeem_gift_certificate(
    payload: GiftRedemptionIn,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    business_id = _visits_business_id(current_user, db, locale)
    try:
        certificate, entry, amount, visits = gifts.redeem(
            db,
            business_id,
            payload.code,
            payload.visit_id,
            payload.amount,
            payload.reason,
            payload.request_key,
            current_user.id,
        )
    except gifts.GiftError as exc:
        _raise_gift(exc, locale)
    return {
        "certificate": gifts.certificate_out(db, certificate, reveal_code=True),
        "entry_id": entry.id,
        "applied_amount": float(amount),
        "applied_visits": visits,
    }


@certificates_router.post(
    "/{certificate_id}/reversals",
    response_model=GiftReversalOut,
    summary="Return one redemption to the balance",
)
def reverse_gift_redemption(
    certificate_id: int,
    payload: GiftReversalIn,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    business_id = _financial_business_id(current_user, db, locale)
    try:
        certificate, entry = gifts.reverse(
            db,
            business_id,
            certificate_id,
            payload.redemption_entry_id,
            payload.request_key,
            payload.reason,
            current_user.id,
        )
    except gifts.GiftError as exc:
        _raise_gift(exc, locale)
    return {
        "certificate": gifts.certificate_out(db, certificate, reveal_code=True),
        "entry_id": entry.id,
        "restored_amount": float(entry.amount_delta),
        "restored_visits": int(entry.units_delta),
    }
