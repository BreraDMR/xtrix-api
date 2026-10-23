"""Public waitlist joins and offers, plus the salon's own waitlist view.

The public side never returns a queue position or another person's contact. Entry tokens
are only for leaving; offer tokens are separate and are minted when the email is rendered.
The staff side is scoped to the caller's business by the same helper as visits and clients.
"""
from datetime import date, timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core import booking_manage_access
from app.api.core import public_booking_service as booking_service
from app.api.core import waitlist_service
from app.api.core.booking_slug import normalize_slug
from app.api.core.clock import utcnow
from app.api.core.i18n import _
from app.api.core.pagination import MAX_PAGE, PaginatedResponse
from app.api.core.permissions import get_user_business_id
from app.api.crud import crud_public_booking
from app.api.middleware.rate_limit import (
    WAITLIST_ACTION_LIMIT,
    WAITLIST_JOIN_LIMIT,
    limiter,
)
from app.db.models.business import Business
from app.db.models.service import Service
from app.db.models.user import User
from app.db.models.waitlist import WaitlistEntry, WaitlistOffer
from app.schemas.waitlist import (
    WaitlistEntryOut,
    WaitlistJoinIn,
    WaitlistJoinOut,
    WaitlistMessageOut,
    WaitlistOfferAcceptOut,
    WaitlistOfferOut,
    WaitlistOfferSummaryOut,
)

router = APIRouter()
staff_router = APIRouter()


def _published(db: Session, slug: str, locale: str) -> Business:
    settings = crud_public_booking.get_enabled_by_slug(db, normalize_slug(slug))
    business = settings.business if settings else None
    if not settings or not business:
        raise HTTPException(status_code=404, detail=_("public_booking_unavailable", locale))
    return business


def _offer_out(db: Session, offer: WaitlistOffer) -> WaitlistOfferOut:
    business, offered_service, staff = waitlist_service.offer_context(db, offer)
    status = offer.status
    if status == "pending" and offer.expires_at <= utcnow():
        waitlist_service.expire_offer(db, offer)
        status = "expired"
    return WaitlistOfferOut(
        business_name=business.name,
        business_phone=business.phone,
        business_address=business.address,
        service_name=offered_service.name,
        staff_name=staff.full_name,
        start=offer.start,
        end=offer.end,
        timezone=business.timezone,
        status=status,
        expires_at=offer.expires_at,
        price=offered_service.price,
        currency=business.currency,
    )


def _accepted_out(db: Session, business: Business, visit) -> WaitlistOfferAcceptOut:
    manage_token, _token = booking_manage_access.issue_token(db, visit)
    accepted = booking_service.public_booking_out(db, business, visit, manage_token)
    return WaitlistOfferAcceptOut(**accepted.model_dump())


@router.post(
    "/{slug}/join",
    response_model=WaitlistJoinOut,
    status_code=202,
    summary="Join the waitlist for a service",
    description=(
        "Stores preferences and consent only. The response never shows a queue or other "
        "clients; repeating the same request returns the existing active entry."
    ),
)
@limiter.limit(WAITLIST_JOIN_LIMIT)
def join_waitlist(
    request: Request,
    slug: str,
    payload: WaitlistJoinIn,
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    business = _published(db, slug, locale)
    try:
        _entry, token, created = waitlist_service.join_waitlist(db, business, payload, locale)
    except waitlist_service.ServiceUnavailable:
        raise HTTPException(status_code=404, detail=_("public_service_unavailable", locale))
    except waitlist_service.StaffUnavailable:
        raise HTTPException(status_code=404, detail=_("public_staff_unavailable", locale))
    return WaitlistJoinOut(
        message=_("waitlist_joined", locale),
        created=created,
        manage_token=token,
    )


@router.post("/leave", response_model=WaitlistMessageOut, summary="Leave the waitlist")
@limiter.limit(WAITLIST_ACTION_LIMIT)
def leave_waitlist(
    request: Request,
    x_waitlist_token: Optional[str] = Header(None, alias="X-Waitlist-Token"),
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    try:
        waitlist_service.leave_waitlist(db, x_waitlist_token)
    except waitlist_service.OfferUnavailable:
        raise HTTPException(status_code=404, detail=_("waitlist_offer_unavailable", locale))
    except waitlist_service.EntryClosed:
        raise HTTPException(status_code=409, detail=_("waitlist_entry_closed", locale))
    return WaitlistMessageOut(message=_("waitlist_left", locale))


@router.get(
    "/offer",
    response_model=WaitlistOfferOut,
    summary="Read a time-limited waitlist offer",
)
@limiter.limit(WAITLIST_ACTION_LIMIT)
def read_offer(
    request: Request,
    x_waitlist_token: Optional[str] = Header(None, alias="X-Waitlist-Token"),
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    try:
        offer = waitlist_service.resolve_offer(db, x_waitlist_token)
    except waitlist_service.OfferUnavailable:
        raise HTTPException(status_code=404, detail=_("waitlist_offer_unavailable", locale))
    return _offer_out(db, offer)


@router.post(
    "/offer/accept",
    response_model=WaitlistOfferAcceptOut,
    summary="Accept an offer with a fresh availability check",
    description=(
        "The offer is locked and the slot is re-checked. Of two confirmations for the same "
        "time only one creates a visit; the other gets a conflict and its entry stays active."
    ),
)
@limiter.limit(WAITLIST_ACTION_LIMIT)
def accept_offer(
    request: Request,
    x_waitlist_token: Optional[str] = Header(None, alias="X-Waitlist-Token"),
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    try:
        visit = waitlist_service.accept_offer(db, x_waitlist_token, locale)
    except waitlist_service.OfferUnavailable:
        raise HTTPException(status_code=404, detail=_("waitlist_offer_unavailable", locale))
    except waitlist_service.OfferExpired:
        raise HTTPException(status_code=409, detail=_("waitlist_offer_expired", locale))
    except waitlist_service.OfferConflict:
        raise HTTPException(status_code=409, detail=_("waitlist_offer_conflict", locale))
    except waitlist_service.OfferDeclined:
        raise HTTPException(status_code=409, detail=_("waitlist_offer_declined", locale))
    except waitlist_service.EntryClosed:
        raise HTTPException(status_code=409, detail=_("waitlist_entry_closed", locale))
    business = db.get(Business, visit.business_id)
    return _accepted_out(db, business, visit)


@router.post(
    "/offer/decline",
    response_model=WaitlistMessageOut,
    summary="Decline one offer without leaving the waitlist",
)
@limiter.limit(WAITLIST_ACTION_LIMIT)
def decline_offer(
    request: Request,
    x_waitlist_token: Optional[str] = Header(None, alias="X-Waitlist-Token"),
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    try:
        waitlist_service.decline_offer(db, x_waitlist_token)
    except waitlist_service.OfferUnavailable:
        raise HTTPException(status_code=404, detail=_("waitlist_offer_unavailable", locale))
    except waitlist_service.OfferExpired:
        raise HTTPException(status_code=409, detail=_("waitlist_offer_expired", locale))
    except waitlist_service.EntryClosed:
        raise HTTPException(status_code=409, detail=_("waitlist_entry_closed", locale))
    return WaitlistMessageOut(message=_("waitlist_offer_declined", locale))


def _staff_business_id(db: Session, user: User, locale: str) -> int:
    from app.api.core.permissions import get_staff_record
    # The waitlist desk belongs to the owner or a standalone specialist.
    if user.role == "employee" or user.employer_id is not None or get_staff_record(db, user):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    business_id = get_user_business_id(user, db)
    if not business_id:
        raise HTTPException(status_code=403, detail=_("business_required", locale))
    return business_id


def _entry_out(
    entry: WaitlistEntry,
    service_name: str,
    specialist_name: Optional[str],
    latest_offer: Optional[WaitlistOffer],
    offer_staff_name: Optional[str],
) -> WaitlistEntryOut:
    return WaitlistEntryOut(
        id=entry.id,
        full_name=entry.full_name,
        email=entry.email,
        phone=entry.phone,
        language=entry.language,
        service_id=entry.service_id,
        service_name=service_name,
        specialist_id=entry.specialist_id,
        specialist_name=specialist_name,
        preferred_date_from=entry.preferred_date_from,
        preferred_date_to=entry.preferred_date_to,
        time_preference=entry.time_preference,
        status=entry.status,
        created_at=entry.created_at,
        latest_offer=(
            WaitlistOfferSummaryOut(
                id=latest_offer.id,
                start=latest_offer.start,
                end=latest_offer.end,
                staff_id=latest_offer.specialist_id,
                staff_name=offer_staff_name or "",
                status=latest_offer.status,
                expires_at=latest_offer.expires_at,
            )
            if latest_offer
            else None
        ),
    )


def _latest_offers(db: Session, entry_ids: list[int]) -> dict[int, WaitlistOffer]:
    if not entry_ids:
        return {}
    rows = (
        db.query(WaitlistOffer)
        .filter(WaitlistOffer.entry_id.in_(entry_ids))
        .order_by(WaitlistOffer.created_at.desc(), WaitlistOffer.id.desc())
        .all()
    )
    latest: dict[int, WaitlistOffer] = {}
    for offer in rows:
        latest.setdefault(offer.entry_id, offer)
    return latest


@staff_router.get(
    "",
    response_model=PaginatedResponse[WaitlistEntryOut],
    summary="List the business's waitlist requests",
)
def list_waitlist(
    status: Optional[Literal["active", "cancelled", "fulfilled"]] = None,
    service_id: Optional[int] = Query(None, gt=0),
    specialist_id: Optional[int] = Query(None, gt=0),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    page: int = Query(1, ge=1, le=MAX_PAGE),
    per_page: int = Query(20, ge=1, le=100),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(
        deps.role_required("admin", "business_owner", "specialist", "employee")
    ),
    locale: str = Depends(deps.get_locale),
):
    business_id = _staff_business_id(db, current_user, locale)
    query = (
        db.query(WaitlistEntry, Service.name)
        .join(Service, Service.id == WaitlistEntry.service_id)
        .filter(WaitlistEntry.business_id == business_id)
    )
    if status:
        query = query.filter(WaitlistEntry.status == status)
    if service_id:
        query = query.filter(WaitlistEntry.service_id == service_id)
    if specialist_id:
        query = query.filter(WaitlistEntry.specialist_id == specialist_id)
    if date_from:
        query = query.filter(
            or_(
                WaitlistEntry.preferred_date_to.is_(None),
                WaitlistEntry.preferred_date_to >= date_from,
            )
        )
    if date_to:
        query = query.filter(WaitlistEntry.preferred_date_from <= date_to)
    total = query.count()
    rows = (
        query.order_by(WaitlistEntry.created_at.desc(), WaitlistEntry.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    entries = [entry for entry, _name in rows]
    names = {entry.id: name for entry, name in rows}
    latest = _latest_offers(db, [entry.id for entry in entries])

    staff_ids = {entry.specialist_id for entry in entries if entry.specialist_id}
    staff_ids.update(offer.specialist_id for offer in latest.values())
    staff_names = {
        user.id: user.full_name
        for user in db.query(User).filter(User.id.in_(staff_ids)).all()
    } if staff_ids else {}

    items = [
        _entry_out(
            entry,
            names[entry.id],
            staff_names.get(entry.specialist_id) if entry.specialist_id else None,
            latest.get(entry.id),
            staff_names.get(latest[entry.id].specialist_id) if entry.id in latest else None,
        )
        for entry in entries
    ]
    return PaginatedResponse[WaitlistEntryOut].create(items, total, page, per_page)


@staff_router.post(
    "/{entry_id}/cancel",
    response_model=WaitlistEntryOut,
    summary="Close one waitlist request",
)
def cancel_waitlist_entry(
    entry_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(
        deps.role_required("admin", "business_owner", "specialist", "employee")
    ),
    locale: str = Depends(deps.get_locale),
):
    business_id = _staff_business_id(db, current_user, locale)
    entry = (
        db.query(WaitlistEntry)
        .filter(
            WaitlistEntry.id == entry_id,
            WaitlistEntry.business_id == business_id,
        )
        .first()
    )
    if entry is None:
        raise HTTPException(status_code=404, detail=_("waitlist_entry_not_found", locale))
    waitlist_service.cancel_entry(db, entry)
    service_name = (
        db.query(Service.name).filter(Service.id == entry.service_id).scalar() or ""
    )
    specialist_name = (
        db.query(User.full_name).filter(User.id == entry.specialist_id).scalar()
        if entry.specialist_id
        else None
    )
    latest = _latest_offers(db, [entry.id]).get(entry.id)
    offer_staff_name = (
        db.query(User.full_name).filter(User.id == latest.specialist_id).scalar()
        if latest
        else None
    )
    return _entry_out(entry, service_name, specialist_name, latest, offer_staff_name)
