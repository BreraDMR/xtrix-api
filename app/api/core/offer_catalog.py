from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional

from app.schemas.offer_catalog import (
    OfferCatalogOut,
    OfferEntitlements,
    OfferOut,
)

CATALOG_VERSION = "2026-10-v1"

# Master offer definitions per D01 decision matrix.
# Every price here is a proposal: D01 (Damir) has not approved the commercial tiers yet,
# so all of them stay draft and are neither published nor purchasable until that decision.
OFFER_PLAN_CODES: Dict[str, str] = {}
OFFERS_DATA: List[Dict] = [
    {
        "code": "crm_starter_czk",
        "product": "crm",
        "name": "CRM Starter (до 100 клиентов)",
        "description": "Базовый тариф CRM для небольших салонов и частных мастеров",
        "status": "draft",
        "price": Decimal("200"),
        "currency": "CZK",
        "interval": "month",
        "entitlements": {
            "max_active_clients": 100,
            "daily_requests_limit": None,
            "features": ["clients", "schedule", "services", "online_booking"],
        },
    },
    {
        "code": "crm_standard_czk",
        "product": "crm",
        "name": "CRM Pro (до 500 клиентов)",
        "description": "Стандартный тариф CRM для растущих салонов",
        "status": "draft",
        "price": Decimal("400"),
        "currency": "CZK",
        "interval": "month",
        "entitlements": {
            "max_active_clients": 500,
            "daily_requests_limit": None,
            "features": ["clients", "schedule", "services", "online_booking", "analytics", "loyalty"],
        },
    },
    {
        "code": "crm_scale_draft_czk",
        "product": "crm",
        "name": "CRM Scale (501–1000 клиентов)",
        "description": "Тариф на согласовании (требуется коммерческое решение D01)",
        "status": "draft",
        "price": Decimal("800"),
        "currency": "CZK",
        "interval": "month",
        "entitlements": {
            "max_active_clients": 1000,
            "daily_requests_limit": None,
            "features": ["clients", "schedule", "services", "online_booking", "analytics", "loyalty", "multi_specialist"],
        },
    },
    {
        "code": "crm_enterprise_draft_czk",
        "product": "crm",
        "name": "CRM Enterprise (>1000 клиентов)",
        "description": "Индивидуальный тариф на согласовании (требуется решение D01)",
        "status": "draft",
        "price": Decimal("1500"),
        "currency": "CZK",
        "interval": "month",
        "entitlements": {
            "max_active_clients": 5000,
            "daily_requests_limit": None,
            "features": ["all"],
        },
    },
    {
        "code": "ai_starter_czk",
        "product": "ai",
        "name": "AI Assistant Starter (20 в день)",
        "description": "Ассистент Сашенька, базовый пакет запросов",
        "status": "draft",
        "price": Decimal("200"),
        "currency": "CZK",
        "interval": "month",
        "entitlements": {
            "max_active_clients": None,
            "daily_requests_limit": 20,
            "features": ["assistant_chat", "smart_scheduling"],
        },
    },
    {
        "code": "ai_pro_czk",
        "product": "ai",
        "name": "AI Assistant Pro (50 в день)",
        "description": "Расширенный пакет ассистента Сашеньки",
        "status": "draft",
        "price": Decimal("400"),
        "currency": "CZK",
        "interval": "month",
        "entitlements": {
            "max_active_clients": None,
            "daily_requests_limit": 50,
            "features": ["assistant_chat", "smart_scheduling", "priority_generation"],
        },
    },
    {
        "code": "ai_voice_draft_czk",
        "product": "ai",
        "name": "AI Voice Unit (Голосовые минуты)",
        "description": "Пакет голосовых минут на согласовании (D01)",
        "status": "draft",
        "price": Decimal("300"),
        "currency": "CZK",
        "interval": "month",
        "entitlements": {
            "max_active_clients": None,
            "daily_requests_limit": 100,
            "features": ["voice_assistant"],
        },
    },
]


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
        offers=offers,
        updated_at=datetime(2026, 10, 20, 0, 0, 0, tzinfo=timezone.utc),
    )


def match_crm_offer_for_clients(active_clients_count: int, allow_draft: bool = False) -> Optional[OfferOut]:
    """Find matching CRM offer for active clients count.

    501-1000 and >1000 tiers are draft (requiring D01 decision) and are never
    treated as free or approved automatically.
    """
    if active_clients_count <= 0:
        return get_offer_by_code("crm_starter_czk")
    if active_clients_count <= 100:
        return get_offer_by_code("crm_starter_czk")
    if active_clients_count <= 500:
        return get_offer_by_code("crm_standard_czk")
    if active_clients_count <= 1000:
        if allow_draft:
            return get_offer_by_code("crm_scale_draft_czk", include_draft=True)
        return None
    if allow_draft:
        return get_offer_by_code("crm_enterprise_draft_czk", include_draft=True)
    return None
