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
from app.api.core import booking_manage_access, booking_manage_policy, notification_events
from app.api.core import public_booking_service as booking_service
from app.api.core.clock import utcnow
from app.api.core.i18n import _
from app.api.crud import crud_public_booking
from app.api.middleware.rate_limit import (
    PUBLIC_BOOKING_MANAGE_ACTION_LIMIT,
    PUBLIC_BOOKING_MANAGE_LIMIT,
    limiter,
)
from app.db.models.booking_manage_token import BookingManageToken
from app.db.models.user import User
from app.db.models.visit import Visit
from app.db.models.visit_event import VisitEvent
from app.schemas.public_booking import PublicBookingManageOut, PublicBookingRescheduleIn

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


def _locked_visit(db: Session, token: BookingManageToken, visit_id: int, locale: str) -> Visit:
    visit = (
        db.query(Visit)
        .filter(Visit.id == visit_id, Visit.deleted_at.is_(None))
        .with_for_update()
        .populate_existing()
        .first()
    )
    if not visit or visit.business_id != token.business_id:
        raise HTTPException(status_code=404, detail=_("booking_manage_link_unavailable", locale))
    return visit


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
    visit = _locked_visit(db, token, visit.id, locale)

    reason = booking_manage_policy.confirmation_block_reason(visit)
    if reason:
        raise HTTPException(status_code=409, detail=_(reason, locale))

    if visit.client_confirmed_at is None:
        visit.client_confirmed_at = utcnow()
        db.commit()
        db.refresh(visit)
    return _summary(token, visit)


@router.post(
    "/reschedule",
    response_model=PublicBookingManageOut,
    summary="Move a booking to another available slot",
    description=(
        "The visit keeps its service and master. The target is checked against the same "
        "availability rules as public booking while both rows are locked; on a conflict "
        "the old time stays unchanged. Repeating the request for the time already stored "
        "returns the booking without a second move or event."
    ),
)
@limiter.limit(PUBLIC_BOOKING_MANAGE_ACTION_LIMIT)
def reschedule_managed_booking(
    request: Request,
    payload: PublicBookingRescheduleIn,
    x_booking_token: Optional[str] = Header(None, alias="X-Booking-Token"),
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    token, visit = _resolve(db, x_booking_token, locale, action="reschedule")
    visit = _locked_visit(db, token, visit.id, locale)

    reason = booking_manage_policy.reschedule_block_reason(visit)
    if reason:
        raise HTTPException(status_code=409, detail=_(reason, locale))

    new_start = payload.start
    if new_start == visit.date_start:
        return _summary(token, visit)

    service = (
        crud_public_booking.get_public_service(db, visit.business_id, visit.service_id)
        if visit.service_id
        else None
    )
    if not service or visit.specialist_id not in crud_public_booking.public_service_master_ids(
        db, visit.business_id, service
    ):
        raise HTTPException(status_code=409, detail=_("booking_reschedule_unavailable", locale))

    # the same row public booking locks before checking a slot, so the two paths can't
    # pick the same free time from under each other
    staff = (
        db.query(User)
        .filter(
            User.id == visit.specialist_id,
            User.deleted_at.is_(None),
            User.archived_at.is_(None),
        )
        .with_for_update()
        .first()
    )
    if not staff:
        raise HTTPException(status_code=409, detail=_("booking_reschedule_unavailable", locale))

    if not booking_service._slot_is_free(
        db,
        visit.business,
        service,
        staff.id,
        new_start,
        exclude_visit_id=visit.id,
    ):
        raise HTTPException(status_code=409, detail=_("public_slot_unavailable", locale))

    previous_start = visit.date_start
    visit.date_start = new_start
    visit.date_end = new_start + timedelta(minutes=service.duration_minutes)
    db.add(
        VisitEvent(
            business_id=visit.business_id,
            visit_id=visit.id,
            event_type="rescheduled",
            actor="client",
            event_key=(
                f"visit:{visit.id}:rescheduled:"
                f"{previous_start.isoformat()}>{new_start.isoformat()}:"
                f"{utcnow().isoformat()}"
            ),
        )
    )
    notification_events.queue_booking_changed(db, visit.business, visit)
    db.commit()
    db.refresh(visit)
    from app.api.core import waitlist_service

    waitlist_service.release_slot_quietly(
        db, visit.business, visit.service_id, visit.specialist_id, previous_start
    )

    try:
        from app.api.core.redis_client import invalidate_visit

        invalidate_visit(visit.id)
    except Exception:
        pass
    return _summary(token, visit)


@router.post(
    "/cancel",
    response_model=PublicBookingManageOut,
    summary="Cancel a booking by its guest token",
    description=(
        "Cancelling twice returns the already cancelled booking without a second event; "
        "the status change and the visit event are committed together."
    ),
)
@limiter.limit(PUBLIC_BOOKING_MANAGE_ACTION_LIMIT)
def cancel_managed_booking(
    request: Request,
    x_booking_token: Optional[str] = Header(None, alias="X-Booking-Token"),
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    token, visit = _resolve(db, x_booking_token, locale, action="cancel")
    visit = _locked_visit(db, token, visit.id, locale)

    if visit.status != "cancelled":
        reason = booking_manage_policy.cancellation_block_reason(visit)
        if reason:
            raise HTTPException(status_code=409, detail=_(reason, locale))
        visit.status = "cancelled"
        from app.api.core import service_payment_service as payments

        payments.mark_attempt_cancelled(db, visit)
        db.add(
            VisitEvent(
                business_id=visit.business_id,
                visit_id=visit.id,
                event_type="cancelled",
                actor="client",
                event_key=f"visit:{visit.id}:cancelled",
            )
        )
        notification_events.queue_booking_cancelled(db, visit.business, visit)
        db.commit()
        db.refresh(visit)
        from app.api.core import waitlist_service

        waitlist_service.release_slot_quietly(
            db, visit.business, visit.service_id, visit.specialist_id, visit.date_start
        )
    return _summary(token, visit)
