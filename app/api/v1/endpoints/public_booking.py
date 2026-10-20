"""Anonymous booking endpoints plus the owner's publication switch.

The public side never takes a business id: the slug is resolved to one published business,
and every lookup below it is scoped to that business.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core.booking_slug import normalize_slug
from app.api.core.i18n import _
from app.api.core.public_booking_access import manage_public_booking_business_id
from app.api.crud import crud_public_booking
from app.db.models.business import Business
from app.db.models.public_booking import PublicBookingSettings
from app.db.models.user import User
from app.schemas.public_booking import (
    PublicBookingSettingsOut,
    PublicBookingSettingsUpdate,
    PublicBusinessOut,
)

router = APIRouter()
settings_router = APIRouter()


def _resolve_published(db: Session, slug: str, locale: str) -> tuple[PublicBookingSettings, Business]:
    """One answer for unknown and disabled links: neither reveals that the salon exists."""
    settings = crud_public_booking.get_enabled_by_slug(db, normalize_slug(slug))
    business = settings.business if settings else None
    if not settings or not business:
        raise HTTPException(status_code=404, detail=_("public_booking_unavailable", locale))
    return settings, business


def _public_business(settings: PublicBookingSettings, business: Business) -> PublicBusinessOut:
    return PublicBusinessOut(
        slug=settings.slug,
        name=business.name,
        business_type=business.business_type,
        address=business.address,
        phone=business.phone,
        email=business.email,
        description=business.description,
        currency=business.currency,
        timezone=business.timezone,
    )


@settings_router.get("", response_model=PublicBookingSettingsOut, summary="Get the public booking link settings")
def get_public_booking_settings(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    business_id = manage_public_booking_business_id(db, current_user, locale)
    settings = crud_public_booking.get_or_create_settings(db, business_id)
    if not settings:
        raise HTTPException(status_code=404, detail=_("business_not_found", locale))
    return settings


@settings_router.put("", response_model=PublicBookingSettingsOut, summary="Change the public booking link")
def update_public_booking_settings(
    payload: PublicBookingSettingsUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    business_id = manage_public_booking_business_id(db, current_user, locale)
    settings = crud_public_booking.get_or_create_settings(db, business_id)
    if not settings:
        raise HTTPException(status_code=404, detail=_("business_not_found", locale))
    try:
        return crud_public_booking.update_settings(db, settings, payload)
    except crud_public_booking.PublicSlugConflict:
        raise HTTPException(status_code=409, detail=_("public_slug_taken", locale))


@router.get("/{slug}", response_model=PublicBusinessOut, summary="Published salon card")
def get_public_business(
    slug: str,
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    settings, business = _resolve_published(db, slug, locale)
    return _public_business(settings, business)
