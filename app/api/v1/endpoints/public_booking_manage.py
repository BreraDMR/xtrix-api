"""Guest-side booking summary selected by a bearer token.

The token always travels in a header, never in the path, so access logs don't keep
a usable management link. The visit and the business are taken from the token; the
response leaves out staff notes, medical data and anyone else's contacts.
"""
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core import booking_manage_access
from app.api.core.i18n import _
from app.api.middleware.rate_limit import PUBLIC_BOOKING_MANAGE_LIMIT, limiter
from app.schemas.public_booking import PublicBookingManageOut

router = APIRouter()


@router.get("", response_model=PublicBookingManageOut, summary="Read a booking by its guest token")
@limiter.limit(PUBLIC_BOOKING_MANAGE_LIMIT)
def get_managed_booking(
    request: Request,
    x_booking_token: Optional[str] = Header(None, alias="X-Booking-Token"),
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    try:
        token, visit = booking_manage_access.resolve_token(db, x_booking_token)
    except booking_manage_access.BookingManageTokenInvalid:
        raise HTTPException(status_code=404, detail=_("booking_manage_link_unavailable", locale))

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
    )
