"""Anonymous booking endpoints plus the owner's publication switch.

The public side never takes a business id: the slug is resolved to one published business,
and every lookup below it is scoped to that business.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core.i18n import _
from app.api.core.public_booking_access import manage_public_booking_business_id
from app.api.crud import crud_public_booking
from app.db.models.user import User
from app.schemas.public_booking import PublicBookingSettingsOut, PublicBookingSettingsUpdate

router = APIRouter()
settings_router = APIRouter()


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
