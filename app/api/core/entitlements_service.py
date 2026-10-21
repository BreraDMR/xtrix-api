from decimal import Decimal
from typing import Any, Dict
from fastapi import HTTPException

from app.api.core.offer_catalog import OFFER_PLAN_CODES, get_offer_by_code
from app.db.models.user import User
from app.schemas.offer_catalog import OfferOut


def resolve_offer_for_checkout(offer_code: str) -> OfferOut:
    """Resolve approved offer for SaaS Stripe checkout.

    Server determines amount, currency, interval, and product.
    Draft offers (requiring D01 commercial approval) cannot be purchased.
    """
    offer = get_offer_by_code(offer_code, include_draft=False)
    if not offer:
        raise HTTPException(
            status_code=400,
            detail=f"Offer '{offer_code}' not found or not approved for purchase",
        )
    if offer.price <= Decimal("0"):
        raise HTTPException(status_code=400, detail="Offer price must be greater than zero")
    if not OFFER_PLAN_CODES.get(offer.code):
        # Without a plan the Stripe webhook could not provision access; stay closed
        # until D01 approves both the price and the plan row it belongs to.
        raise HTTPException(status_code=400, detail="Offer is not mapped to a subscription plan")
    return offer


def build_checkout_payload_for_offer(
    user: User,
    offer: OfferOut,
    frontend_url: str,
    plan_id: int,
) -> Dict[str, Any]:
    """Construct Stripe line items and metadata strictly from approved server offer.

    Guarantees that price and currency are defined by server offer catalog,
    and isolates CRM and AI products in checkout attempt metadata.
    """
    amount_in_cents = int((offer.price * 100).quantize(Decimal("1")))
    interval = "month" if offer.interval == "month" else "year"

    return dict(
        line_items=[
            {
                "price_data": {
                    "currency": offer.currency.lower(),
                    "product_data": {
                        "name": offer.name,
                        "description": offer.description or f"{offer.product.upper()} Plan",
                    },
                    "unit_amount": amount_in_cents,
                    "recurring": {
                        "interval": interval,
                    },
                },
                "quantity": 1,
            }
        ],
        mode="subscription",
        success_url=f"{frontend_url}/subscription?success=true&session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{frontend_url}/subscription?canceled=true",
        metadata={
            "user_id": str(user.id),
            "plan_id": str(plan_id),
            "offer_code": offer.code,
            "product": offer.product,
        },
        subscription_data={
            "metadata": {
                "user_id": str(user.id),
                "plan_id": str(plan_id),
                "offer_code": offer.code,
                "product": offer.product,
            }
        },
    )
