"""Owner-facing campaign workflow and the public unsubscribe link.

Campaigns are created as drafts, previewed against live consent, frozen at confirmation and
only then can the owner queue them into the shared outbox. No endpoint sends by itself.
"""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core import campaign_service
from app.api.core.i18n import _
from app.api.core.notification_access import notification_business_id
from app.api.core.pagination import MAX_PAGE, PaginatedResponse, PaginationParams
from app.db.models.campaign import Campaign
from app.db.models.client import Client
from app.db.models.client_business_relation import ClientBusinessRelation
from app.db.models.marketing_consent import MarketingConsent
from app.db.models.user import User
from app.schemas.campaign import (
    CampaignIn,
    CampaignOut,
    CampaignPreviewOut,
    CampaignStatsOut,
    CampaignUpdate,
    MarketingConsentOut,
    PublicUnsubscribeOut,
    UnsubscribeIn,
)

router = APIRouter()
staff_router = APIRouter()
consent_router = APIRouter()

CampaignStatus = Literal["draft", "confirmed", "sending", "sent", "cancelled"]


def _out(campaign: Campaign) -> CampaignOut:
    return CampaignOut.model_validate(campaign, from_attributes=True)


def _preview_out(data: dict) -> CampaignPreviewOut:
    return CampaignPreviewOut(**data)


def _client_in_business(
    db: Session, business_id: int, client_id: int, locale: str
) -> Client:
    client = (
        db.query(Client)
        .join(
            ClientBusinessRelation,
            ClientBusinessRelation.client_id == Client.id,
        )
        .filter(
            Client.id == client_id,
            Client.deleted_at.is_(None),
            ClientBusinessRelation.business_id == business_id,
        )
        .first()
    )
    if client is None:
        raise HTTPException(status_code=404, detail=_("client_not_found", locale))
    return client


def _consent_out(consent: MarketingConsent) -> MarketingConsentOut:
    return MarketingConsentOut.model_validate(consent, from_attributes=True)


@router.get("/unsubscribe", response_model=PublicUnsubscribeOut, summary="Preview an unsubscribe link")
def preview_unsubscribe(
    token: str = Query(..., min_length=8, max_length=500),
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    consent, _campaign = campaign_service.consent_from_token(db, locale, token)
    business = consent.business
    if business is None:
        raise HTTPException(status_code=400, detail=_("invalid_unsubscribe_token", locale))
    return PublicUnsubscribeOut(
        business_name=business.name,
        email=campaign_service.mask_email(
            consent.client.contact_email if consent.client else ""
        ),
        status=consent.status,
        channel=consent.channel,
    )


@router.post("/unsubscribe", response_model=PublicUnsubscribeOut, summary="Unsubscribe with a signed link")
def unsubscribe(
    payload: UnsubscribeIn,
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    consent = campaign_service.unsubscribe_by_token(db, locale, payload.token)
    db.commit()
    db.refresh(consent)
    business = consent.business
    if business is None:
        raise HTTPException(status_code=400, detail=_("invalid_unsubscribe_token", locale))
    return PublicUnsubscribeOut(
        business_name=business.name,
        email=campaign_service.mask_email(
            consent.client.contact_email if consent.client else ""
        ),
        status=consent.status,
        channel=consent.channel,
    )


@staff_router.get("", response_model=PaginatedResponse[CampaignOut], summary="List campaigns")
def list_campaigns(
    status: Optional[CampaignStatus] = Query(None),
    page: int = Query(1, ge=1, le=MAX_PAGE),
    per_page: int = Query(20, ge=1, le=100),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    business_id = notification_business_id(db, current_user, locale)
    pagination = PaginationParams(page=page, per_page=per_page)
    query = db.query(Campaign).filter(Campaign.business_id == business_id)
    if status:
        query = query.filter(Campaign.status == status)
    total = query.count()
    rows = (
        query.order_by(desc(Campaign.created_at), desc(Campaign.id))
        .offset(pagination.offset)
        .limit(pagination.limit)
        .all()
    )
    return PaginatedResponse.create(
        items=[_out(row) for row in rows],
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
    )


@staff_router.post("", response_model=CampaignOut, status_code=201, summary="Create a campaign draft")
def create_campaign(
    payload: CampaignIn,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    business_id = notification_business_id(db, current_user, locale)
    campaign = campaign_service.create_campaign(db, business_id, current_user.id, payload)
    db.commit()
    db.refresh(campaign)
    return _out(campaign)


@staff_router.get("/{campaign_id}", response_model=CampaignOut, summary="Get a campaign")
def get_campaign(
    campaign_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    business_id = notification_business_id(db, current_user, locale)
    return _out(campaign_service.campaign_for_business(db, campaign_id, business_id, locale))


@staff_router.patch("/{campaign_id}", response_model=CampaignOut, summary="Edit a campaign draft")
def update_campaign(
    campaign_id: int,
    payload: CampaignUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    business_id = notification_business_id(db, current_user, locale)
    campaign = campaign_service.campaign_for_business(db, campaign_id, business_id, locale)
    fields = payload.model_dump(exclude_unset=True)
    if fields:
        campaign_service.update_campaign(db, campaign, fields, locale)
    db.commit()
    db.refresh(campaign)
    return _out(campaign)


@staff_router.get("/{campaign_id}/preview", response_model=CampaignPreviewOut, summary="Preview audience and text")
def preview_campaign(
    campaign_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    business_id = notification_business_id(db, current_user, locale)
    campaign = campaign_service.campaign_for_business(db, campaign_id, business_id, locale)
    return _preview_out(campaign_service.preview_campaign(db, campaign, locale))


@staff_router.post("/{campaign_id}/confirm", response_model=CampaignOut, summary="Freeze audience and text")
def confirm_campaign(
    campaign_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    business_id = notification_business_id(db, current_user, locale)
    campaign = campaign_service.campaign_for_business(db, campaign_id, business_id, locale)
    campaign_service.confirm_campaign(db, campaign, current_user, locale)
    db.commit()
    db.refresh(campaign)
    return _out(campaign)


@staff_router.post("/{campaign_id}/send", response_model=CampaignOut, summary="Queue a confirmed campaign")
def send_campaign(
    campaign_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    business_id = notification_business_id(db, current_user, locale)
    campaign = campaign_service.campaign_for_business(db, campaign_id, business_id, locale)
    campaign_service.dispatch_campaign(db, campaign, locale)
    db.commit()
    db.refresh(campaign)
    return _out(campaign)


@staff_router.post("/{campaign_id}/cancel", response_model=CampaignOut, summary="Cancel a campaign")
def cancel_campaign(
    campaign_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    business_id = notification_business_id(db, current_user, locale)
    campaign = campaign_service.campaign_for_business(db, campaign_id, business_id, locale)
    campaign_service.cancel_campaign(db, campaign, locale)
    db.commit()
    db.refresh(campaign)
    return _out(campaign)


@staff_router.get("/{campaign_id}/stats", response_model=CampaignStatsOut, summary="Delivery summary")
def campaign_stats(
    campaign_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    business_id = notification_business_id(db, current_user, locale)
    campaign = campaign_service.campaign_for_business(db, campaign_id, business_id, locale)
    stats = campaign_service.campaign_stats(db, campaign)
    db.commit()
    return CampaignStatsOut(**stats)


@consent_router.get(
    "/{client_id}/marketing-consent",
    response_model=MarketingConsentOut,
    summary="Read a client's marketing consent",
)
def get_marketing_consent(
    client_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    business_id = notification_business_id(db, current_user, locale)
    _client_in_business(db, business_id, client_id, locale)
    consent = (
        db.query(MarketingConsent)
        .filter(
            MarketingConsent.business_id == business_id,
            MarketingConsent.client_id == client_id,
            MarketingConsent.channel == "email",
        )
        .first()
    )
    if consent is None:
        raise HTTPException(
            status_code=404, detail=_("marketing_consent_not_found", locale)
        )
    return _consent_out(consent)


@consent_router.post(
    "/{client_id}/marketing-consent/revoke",
    response_model=MarketingConsentOut,
    summary="Revoke a client's marketing consent",
)
def revoke_marketing_consent(
    client_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    business_id = notification_business_id(db, current_user, locale)
    _client_in_business(db, business_id, client_id, locale)
    consent = (
        db.query(MarketingConsent)
        .filter(
            MarketingConsent.business_id == business_id,
            MarketingConsent.client_id == client_id,
            MarketingConsent.channel == "email",
        )
        .first()
    )
    if consent is None:
        raise HTTPException(
            status_code=404, detail=_("marketing_consent_not_found", locale)
        )
    campaign_service.revoke_consent(db, consent, source="owner_request")
    db.commit()
    db.refresh(consent)
    return _consent_out(consent)
