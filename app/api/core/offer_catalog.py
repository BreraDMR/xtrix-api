from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional

from app.api.core.config import settings

from app.schemas.offer_catalog import (
    OfferCatalogOut,
    OfferEntitlements,
    OfferOut,
)

CATALOG_VERSION = "2026-10-pilot-v2"

# Approved monthly tiers. Publishing prices does not enable payment collection.
OFFER_PLAN_CODES: Dict[str, str] = {}
OFFERS_DATA: List[Dict] = []
for level, name, visits, price, daily in [
    ("starter", "Старт", 100, "200", 20),
    ("standard", "Стандарт", 300, "400", 50),
    ("business", "Бизнес", 700, "700", 100),
    ("large_business", "Большой бизнес", 2000, "1200", 200),
]:
    for product in ("crm", "ai"):
        code_level = "pro" if product == "ai" and level == "standard" else level
        OFFERS_DATA.append(dict(
            code=f"{product}_{code_level}_czk", product=product, name=name,
            description=None, status="approved", price=Decimal(price), currency="CZK", interval="month",
            entitlements=dict(max_monthly_visits=visits if product == "crm" else None,
                              daily_requests_limit=daily if product == "ai" else None,
                              features=["clients", "schedule", "services", "online_booking"] if product == "crm" else ["assistant_chat", "smart_scheduling"]),
        ))


def get_all_offers() -> List[OfferOut]:
    return [OfferOut(**item) for item in OFFERS_DATA]


def get_approved_offers() -> List[OfferOut]:
    return [OfferOut(**item) for item in OFFERS_DATA if item["status"] == "approved"]


def get_offer_by_code(code: str, include_draft: bool = False) -> Optional[OfferOut]:
    for item in OFFERS_DATA:
        if item["code"] == code:
            if item["status"] == "approved" or include_draft:
                return OfferOut(**item)
    return None


def get_offer_catalog(include_draft: bool = False) -> OfferCatalogOut:
    offers = get_all_offers() if include_draft else get_approved_offers()
    return OfferCatalogOut(
        version=CATALOG_VERSION,
        pilot_mode=settings.pilot_mode_enabled,
        checkout_available=bool(settings.stripe_api_key and not settings.pilot_mode_enabled),
        registration_requires_invitation=settings.pilot_mode_enabled,
        email_available=bool(not settings.pilot_mode_enabled and settings.smtp_host and settings.smtp_from),
        offers=offers,
        updated_at=datetime(2026, 10, 21, 0, 0, 0, tzinfo=timezone.utc),
    )


def match_crm_offer_for_visits(visits_count: int) -> Optional[OfferOut]:
    for offer in get_approved_offers():
        if offer.product == "crm" and visits_count <= offer.entitlements.max_monthly_visits:
            return offer
    return None


def match_crm_offer_for_clients(active_clients_count: int, allow_draft: bool = False) -> Optional[OfferOut]:
    # Compatibility for older callers; the commercial unit is now visits.
    return match_crm_offer_for_visits(active_clients_count)
