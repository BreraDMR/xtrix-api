from typing import Optional
from fastapi import APIRouter, HTTPException, Query

from app.api.core.offer_catalog import get_offer_by_code, get_offer_catalog
from app.schemas.offer_catalog import OfferCatalogOut, OfferOut, ProductType

router = APIRouter()


@router.get("", response_model=OfferCatalogOut, summary="Get public approved offer catalog")
@router.get("/", response_model=OfferCatalogOut, include_in_schema=False)
def list_offers(
    product: Optional[ProductType] = Query(None, description="Optional product filter: crm or ai"),
):
    """Public catalog of approved SaaS offers for CRM and AI products.

    Per D01 policy:
    1. Only approved tiers are published. Drafts are excluded from anonymous visitors.
    2. Zero business, customer or private tenant data is exposed.
    """
    catalog = get_offer_catalog(include_draft=False)
    if product is not None:
        filtered = [offer for offer in catalog.offers if offer.product == product]
        return OfferCatalogOut(
            version=catalog.version,
            offers=filtered,
            updated_at=catalog.updated_at,
        )
    return catalog


@router.get("/{code}", response_model=OfferOut, summary="Get single approved offer by code")
def get_offer(code: str):
    """Fetch single approved offer by code."""
    offer = get_offer_by_code(code, include_draft=False)
    if not offer:
        raise HTTPException(status_code=404, detail="Offer not found")
    return offer
