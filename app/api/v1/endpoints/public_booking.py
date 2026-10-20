"""Anonymous booking endpoints plus the owner's publication switch.

The public side never takes a business id: the slug is resolved to one published business,
and every lookup below it is scoped to that business.
"""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core.availability import booking_window, collect_slots
from app.api.core.booking_slug import normalize_slug
from app.api.core.clock import utcnow
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
    PublicDaySlotsOut,
    PublicServiceOut,
    PublicSlotOut,
    PublicSlotsOut,
    PublicStaffOut,
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


@router.get("/{slug}/services", response_model=list[PublicServiceOut], summary="Services open for online booking")
def list_public_services(
    slug: str,
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    _settings, business = _resolve_published(db, slug, locale)
    return [
        PublicServiceOut(
            id=service.id,
            name=service.name,
            description=service.description,
            duration_minutes=service.duration_minutes,
            price=service.price,
        )
        for service in crud_public_booking.list_public_services(db, business.id)
    ]


@router.get("/{slug}/staff", response_model=list[PublicStaffOut], summary="Masters of the selected service")
def list_public_staff(
    slug: str,
    service_id: int = Query(..., gt=0),
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    _settings, business = _resolve_published(db, slug, locale)
    service = crud_public_booking.get_public_service(db, business.id, service_id)
    if not service:
        raise HTTPException(status_code=404, detail=_("public_service_unavailable", locale))
    people = crud_public_booking.list_public_staff(db, business.id, service)
    positions = crud_public_booking.staff_positions(db, [person.id for person in people])
    return [
        PublicStaffOut(
            id=person.id,
            full_name=person.full_name,
            position=positions.get(person.id),
        )
        for person in people
    ]


@router.get("/{slug}/slots", response_model=PublicSlotsOut, summary="Free slots by salon-local day")
def list_public_slots(
    slug: str,
    service_id: int = Query(..., gt=0),
    staff_id: int | None = Query(None, gt=0),
    date_from: date = Query(...),
    date_to: date | None = Query(None),
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    _settings, business = _resolve_published(db, slug, locale)
    service = crud_public_booking.get_public_service(db, business.id, service_id)
    if not service:
        raise HTTPException(status_code=404, detail=_("public_service_unavailable", locale))

    last_requested = date_to or date_from
    if last_requested < date_from or (last_requested - date_from).days > 13:
        raise HTTPException(status_code=422, detail=_("public_invalid_date_range", locale))

    people = crud_public_booking.list_public_staff(db, business.id, service)
    if staff_id is not None:
        people = [person for person in people if person.id == staff_id]
        if not people:
            raise HTTPException(status_code=404, detail=_("public_staff_unavailable", locale))
    master_ids = [person.id for person in people]

    now = utcnow()
    first, last, _today = booking_window(
        date_from,
        last_requested,
        business.timezone,
        business.booking_horizon_days,
        now=now,
    )
    if last < first or not master_ids:
        return PublicSlotsOut(timezone=business.timezone, days=[])

    schedules, exceptions, blocks, busy = crud_public_booking.availability_sources(
        db, master_ids, first, last, business.timezone
    )
    slots = collect_slots(
        date_from=first,
        date_to=last,
        timezone_name=business.timezone,
        horizon_days=business.booking_horizon_days,
        lead_time_minutes=business.booking_lead_time_minutes,
        duration_minutes=service.duration_minutes,
        buffer_before_minutes=service.buffer_before_minutes or 0,
        buffer_after_minutes=service.buffer_after_minutes or 0,
        master_ids=master_ids,
        schedules=schedules,
        exceptions=exceptions,
        blocks=blocks,
        busy=busy,
        now=now,
    )

    by_day: dict[date, list[PublicSlotOut]] = {first + timedelta(days=offset): [] for offset in range((last - first).days + 1)}
    for slot in slots:
        by_day[slot.day].append(PublicSlotOut(start=slot.start, staff_id=slot.staff_id))
    return PublicSlotsOut(
        timezone=business.timezone,
        days=[PublicDaySlotsOut(date=day, slots=by_day[day]) for day in sorted(by_day)],
    )
