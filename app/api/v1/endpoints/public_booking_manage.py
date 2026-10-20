"""Guest-side booking actions selected by a bearer token.

The token always travels in a header, never in the path, so access logs don't keep
a usable management link. The visit and the business are taken from the token; the
response leaves out staff notes, medical data and anyone else's contacts.
"""
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core import booking_manage_access, booking_manage_policy
from app.api.core.clock import utcnow
from app.api.core.i18n import _
from app.api.middleware.rate_limit import (
    PUBLIC_BOOKING_MANAGE_ACTION_LIMIT,
    PUBLIC_BOOKING_MANAGE_LIMIT,
    limiter,
)
from app.db.models.booking_manage_token import BookingManageToken
from app.db.models.visit import Visit
from app.schemas.public_booking import PublicBookingManageOut

router = APIRouter()


def _resolve(
    db: Session,
    raw_token: Optional[str],
    locale: str,
    action: Optional[str] = None,
) -> tuple[BookingManageToken, Visit]:
    try:
        return booking_manage_access.resolve_token(db, raw_token, action)
    except booking_manage_access.BookingManageTokenInvalid:
        raise HTTPException(status_code=404, detail=_("booking_manage_link_unavailable", locale))


def _summary(token: BookingManageToken, visit: Visit) -> PublicBookingManageOut:
    business = visit.business
    staff = visit.specialist
    return PublicBookingManageOut(
        booking_id=visit.id,
        business_name=business.name,
        business_phone=business.phone,
        business_address=business.address,
        service_name=visit.service_name or "",
        staff_name=staff.full_name if staff else "",
        start=visit.date_start,
        end=visit.date_end or visit.date_start + timedelta(minutes=60),
        timezone=business.timezone,
        status=visit.status or "planned",
        price=visit.price,
        currency=business.currency,
        token_expires_at=token.expires_at,
        confirmed_at=visit.client_confirmed_at,
    )


@router.get("", response_model=PublicBookingManageOut, summary="Read a booking by its guest token")
@limiter.limit(PUBLIC_BOOKING_MANAGE_LIMIT)
def get_managed_booking(
    request: Request,
    x_booking_token: Optional[str] = Header(None, alias="X-Booking-Token"),
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    token, visit = _resolve(db, x_booking_token, locale)
    return _summary(token, visit)


@router.post(
    "/confirm",
    response_model=PublicBookingManageOut,
    summary="Confirm a booking by its guest token",
    description="Repeating the call keeps the first confirmation and returns the same booking.",
)
@limiter.limit(PUBLIC_BOOKING_MANAGE_ACTION_LIMIT)
def confirm_managed_booking(
    request: Request,
    x_booking_token: Optional[str] = Header(None, alias="X-Booking-Token"),
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    token, visit = _resolve(db, x_booking_token, locale, action="confirm")
    visit = (
        db.query(Visit)
        .filter(Visit.id == visit.id, Visit.deleted_at.is_(None))
        .with_for_update()
        .populate_existing()
        .first()
    )
    if not visit or visit.business_id != token.business_id:
        raise HTTPException(status_code=404, detail=_("booking_manage_link_unavailable", locale))

    reason = booking_manage_policy.confirmation_block_reason(visit)
    if reason:
        raise HTTPException(status_code=409, detail=_(reason, locale))

    if visit.client_confirmed_at is None:
        visit.client_confirmed_at = utcnow()
        db.commit()
        db.refresh(visit)
    return _summary(token, visit)
