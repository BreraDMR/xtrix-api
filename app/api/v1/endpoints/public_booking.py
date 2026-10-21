"""Anonymous booking endpoints plus the owner's publication switch.

The public side never takes a business id: the slug is resolved to one published business,
and every lookup below it is scoped to that business.
"""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core import booking_manage_access
from app.api.core import public_booking_service as booking_service
from app.api.core import service_payment_service as payments
from app.api.core.availability import (
    ChainTiming,
    booking_window,
    collect_chain_slots,
    collect_slots,
)
from app.api.core.booking_slug import normalize_slug
from app.api.core.clock import utcnow
from app.api.core.i18n import _
from app.api.core.public_booking_access import manage_public_booking_business_id
from app.api.core.service_payment_provider import get_service_payment_provider
from app.api.crud import crud_public_booking
from app.api.middleware.rate_limit import (
    PUBLIC_BOOKING_CREATE_LIMIT,
    PUBLIC_BOOKING_READ_LIMIT,
    PUBLIC_BOOKING_SLOTS_LIMIT,
    limiter,
)
from app.db.models.business import Business
from app.db.models.public_booking import PublicBookingSettings
from app.db.models.user import User
from app.schemas.public_booking import (
    PublicBookingCreate,
    PublicBookingOut,
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
    from app.api.core.subscription_access import enforce_business_access
    enforce_business_access(db, business.id)
    return settings, business


def _settings_out(db: Session, settings: PublicBookingSettings) -> PublicBookingSettingsOut:
    has_service, has_schedule = crud_public_booking.public_booking_readiness(db, settings.business_id)
    return PublicBookingSettingsOut(
        slug=settings.slug,
        is_enabled=settings.is_enabled,
        has_bookable_service=has_service,
        has_schedule=has_schedule,
        deposit_type=settings.deposit_type or "none",
        deposit_value=float(settings.deposit_value or 0),
        deposit_hold_minutes=int(settings.deposit_hold_minutes or 30),
        payments_available=get_service_payment_provider() is not None,
    )


def _public_business(settings: PublicBookingSettings, business: Business) -> PublicBusinessOut:
    # a policy without a working provider is not shown as a promise to the guest
    payments_available = get_service_payment_provider() is not None
    deposit_type = settings.deposit_type if payments_available else "none"
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
        deposit_type=deposit_type or "none",
        deposit_value=float(settings.deposit_value or 0) if deposit_type != "none" else 0,
        deposit_hold_minutes=int(settings.deposit_hold_minutes or 30),
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
    return _settings_out(db, settings)


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
        updated = crud_public_booking.update_settings(db, settings, payload)
    except crud_public_booking.PublicSlugConflict:
        raise HTTPException(status_code=409, detail=_("public_slug_taken", locale))
    except crud_public_booking.DepositPolicyInvalid:
        db.rollback()
        raise HTTPException(status_code=422, detail=_("deposit_invalid", locale))
    return _settings_out(db, updated)


@router.get("/{slug}", response_model=PublicBusinessOut, summary="Published salon card")
@limiter.limit(PUBLIC_BOOKING_READ_LIMIT)
def get_public_business(
    request: Request,
    slug: str,
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    settings, business = _resolve_published(db, slug, locale)
    return _public_business(settings, business)


@router.get("/{slug}/services", response_model=list[PublicServiceOut], summary="Services open for online booking")
@limiter.limit(PUBLIC_BOOKING_READ_LIMIT)
def list_public_services(
    request: Request,
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
@limiter.limit(PUBLIC_BOOKING_READ_LIMIT)
def list_public_staff(
    request: Request,
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
@limiter.limit(PUBLIC_BOOKING_SLOTS_LIMIT)
def list_public_slots(
    request: Request,
    slug: str,
    service_id: int = Query(..., gt=0),
    staff_id: int | None = Query(None, gt=0),
    date_from: date = Query(...),
    date_to: date | None = Query(None),
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    _settings, business = _resolve_published(db, slug, locale)
    payments.expire_stale_holds(db, business_id=business.id)
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


@router.get(
    "/{slug}/chain-slots",
    response_model=PublicSlotsOut,
    summary="Free slots for a chain of services with one master",
)
@limiter.limit(PUBLIC_BOOKING_SLOTS_LIMIT)
def list_public_chain_slots(
    request: Request,
    slug: str,
    service_ids: list[int] = Query(..., min_length=1, max_length=20),
    staff_id: int | None = Query(None, gt=0),
    date_from: date = Query(...),
    date_to: date | None = Query(None),
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    _settings, business = _resolve_published(db, slug, locale)
    payments.expire_stale_holds(db, business_id=business.id)
    services = [
        crud_public_booking.get_public_service(db, business.id, service_id)
        for service_id in service_ids
    ]
    if any(service is None for service in services):
        raise HTTPException(status_code=404, detail=_("public_service_unavailable", locale))

    last_requested = date_to or date_from
    if last_requested < date_from or (last_requested - date_from).days > 13:
        raise HTTPException(status_code=422, detail=_("public_invalid_date_range", locale))

    master_sets = [
        crud_public_booking.public_service_master_ids(db, business.id, service)
        for service in services
    ]
    master_ids = set.intersection(*master_sets) if master_sets else set()
    if staff_id is not None:
        master_ids &= {staff_id}
        if not master_ids:
            raise HTTPException(status_code=404, detail=_("public_staff_unavailable", locale))
    if not master_ids:
        return PublicSlotsOut(timezone=business.timezone, days=[])

    now = utcnow()
    first, last, _today = booking_window(
        date_from,
        last_requested,
        business.timezone,
        business.booking_horizon_days,
        now=now,
    )
    if last < first:
        return PublicSlotsOut(timezone=business.timezone, days=[])

    schedules, exceptions, blocks, busy = crud_public_booking.availability_sources(
        db, sorted(master_ids), first, last, business.timezone
    )
    timings = [
        ChainTiming(
            duration_minutes=service.duration_minutes,
            buffer_before_minutes=service.buffer_before_minutes or 0,
            buffer_after_minutes=service.buffer_after_minutes or 0,
        )
        for service in services
    ]
    slots = collect_chain_slots(
        date_from=first,
        date_to=last,
        timezone_name=business.timezone,
        horizon_days=business.booking_horizon_days,
        lead_time_minutes=business.booking_lead_time_minutes,
        timings=timings,
        master_ids=sorted(master_ids),
        schedules=schedules,
        exceptions=exceptions,
        blocks=blocks,
        busy=busy,
        now=now,
    )

    by_day: dict[date, list[PublicSlotOut]] = {
        first + timedelta(days=offset): []
        for offset in range((last - first).days + 1)
    }
    for slot in slots:
        by_day[slot.day].append(PublicSlotOut(start=slot.start, staff_id=slot.staff_id))
    return PublicSlotsOut(
        timezone=business.timezone,
        days=[PublicDaySlotsOut(date=day, slots=by_day[day]) for day in sorted(by_day)],
    )


@router.post(
    "/{slug}/bookings",
    response_model=PublicBookingOut,
    summary="Book an available slot",
    description=(
        "Stores the visit as planned. No email confirmation is sent until the notification "
        "relay is connected, so the guest sees only the stored booking."
    ),
)
@limiter.limit(PUBLIC_BOOKING_CREATE_LIMIT)
def create_public_booking(
    request: Request,
    slug: str,
    payload: PublicBookingCreate,
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    _settings, business = _resolve_published(db, slug, locale)
    try:
        visit = booking_service.submit_booking(db, business, payload, locale)
    except booking_service.ServiceUnavailable:
        raise HTTPException(status_code=404, detail=_("public_service_unavailable", locale))
    except booking_service.StaffUnavailable:
        raise HTTPException(status_code=404, detail=_("public_staff_unavailable", locale))
    except booking_service.RequestIdConflict:
        raise HTTPException(status_code=409, detail=_("public_booking_request_conflict", locale))
    except booking_service.SlotUnavailable:
        raise HTTPException(status_code=409, detail=_("public_slot_unavailable", locale))
    except booking_service.RequestInProgress:
        raise HTTPException(status_code=409, detail=_("public_booking_in_progress", locale))
    except payments.PaymentUnavailable:
        db.rollback()
        raise HTTPException(status_code=502, detail=_("public_payment_unavailable", locale))

    manage_token, _token = booking_manage_access.issue_token(db, visit)
    return booking_service.public_booking_out(db, business, visit, manage_token)
