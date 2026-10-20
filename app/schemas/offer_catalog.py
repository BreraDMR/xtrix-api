from decimal import Decimal
from typing import List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.datetimes import UtcDatetime

ProductType = Literal["crm", "ai"]
OfferStatus = Literal["approved", "draft"]
BillingInterval = Literal["month", "year"]


class OfferEntitlements(BaseModel):
    max_active_clients: Optional[int] = Field(
        default=None,
        ge=1,
        description="Max active clients allowed in period for CRM",
    )
    daily_requests_limit: Optional[int] = Field(
        default=None,
        ge=1,
        description="Daily requests limit for AI assistant",
    )
    features: List[str] = Field(
        default_factory=list,
        description="List of included feature keys",
    )
    model_config = ConfigDict(extra="ignore")


class OfferOut(BaseModel):
    code: str = Field(..., max_length=50, description="Unique offer code, e.g. crm_starter_czk")
    product: ProductType = Field(..., description="Product line: crm or ai")
    name: str = Field(..., max_length=255, description="Display name of offer")
    description: Optional[str] = Field(default=None, max_length=1000)
    status: OfferStatus = Field(default="approved", description="Offer status: approved or draft")
    price: Decimal = Field(..., ge=0, description="Price in currency units")
    currency: str = Field(..., min_length=3, max_length=3, description="ISO currency code, e.g. CZK")
    interval: BillingInterval = Field(default="month", description="Billing interval: month or year")
    entitlements: OfferEntitlements = Field(default_factory=OfferEntitlements)

    model_config = ConfigDict(from_attributes=True, extra="ignore")


class OfferCatalogOut(BaseModel):
    version: str = Field(..., description="Offer catalog version string, e.g. 2026-10-v1")
    offers: List[OfferOut] = Field(default_factory=list, description="List of offers")
    updated_at: Optional[UtcDatetime] = Field(default=None, description="Catalog last update timestamp")

    model_config = ConfigDict(from_attributes=True)


class CrmUsageOut(BaseModel):
    active_clients: int
    limit: Optional[int] = None
    offer_code: Optional[str] = None
    is_within_limit: bool = True


class AiUsageOut(BaseModel):
    daily_requests_used: int
    daily_requests_limit: Optional[int] = None
    offer_code: Optional[str] = None
    is_within_limit: bool = True


class EntitlementsUsageOut(BaseModel):
    period: str
    daily_period: str
    is_payer: bool
    can_manage_billing: bool
    crm: CrmUsageOut
    ai: AiUsageOut

    model_config = ConfigDict(from_attributes=True)

