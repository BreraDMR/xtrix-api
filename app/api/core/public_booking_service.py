"""Guest booking submission.

The order matters: the retry key is claimed in the same transaction as the visit, and
the master's row is locked before the slot is checked again. Two taps on "Book" therefore
produce at most one visit, while a changed body with the same key is rejected.
"""
import hashlib
import json
from datetime import date, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.core.availability import (
    ChainTiming,
    booking_window,
    chain_start_available,
    collect_slots,
    salon_zone,
)
from app.api.core.clock import utcnow
from app.api.core import campaign_service, notification_events, service_payment_service as payments
from app.api.core.visit_chain import apply_chain, service_line, total_minutes
from app.api.crud import crud_public_booking
from app.db.models.public_booking import PublicBookingRequest
from app.db.models.user import User
from app.db.models.visit import Visit
from app.schemas.public_booking import (
    PublicBookingCreate,
    PublicBookingItemOut,
    PublicBookingOut,
)


class ServiceUnavailable(Exception):
    pass


class StaffUnavailable(Exception):
    pass


class SlotUnavailable(Exception):
    pass


class RequestIdConflict(Exception):
    pass


class RequestInProgress(Exception):
    pass


def request_hash(payload: PublicBookingCreate) -> str:
    canonical = json.dumps(
        payload.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _replay(db: Session, row: PublicBookingRequest, digest: str) -> Visit:
    if row.request_hash != digest:
        raise RequestIdConflict()
    if not row.visit_id:
        raise RequestInProgress()
    visit = db.query(Visit).filter(Visit.id == row.visit_id).first()
    if not visit:
        raise RequestInProgress()
    return visit


def _slot_is_free(
    db: Session,
    business,
    service,
    staff_id: int,
    start,
    exclude_visit_id: int | None = None,
) -> bool:
    zone = salon_zone(business.timezone)
    day: date = start.replace(tzinfo=timezone.utc).astimezone(zone).date()
    first, last, _today = booking_window(
        day, day, business.timezone, business.booking_horizon_days
    )
    if last < first:
        return False
    schedules, exceptions, blocks, busy = crud_public_booking.availability_sources(
        db, [staff_id], first, last, business.timezone, exclude_visit_id=exclude_visit_id
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
        master_ids=[staff_id],
        schedules=schedules,
        exceptions=exceptions,
        blocks=blocks,
        busy=busy,
    )
    return any(slot.staff_id == staff_id and slot.start == start for slot in slots)


def _chain_is_free(db: Session, business, services, staff_id: int, start) -> bool:
    """The whole chain must fit one master, item by item, with each service's buffers."""
    lines = [service_line(service, staff_id) for service in services]
    return snapshot_chain_is_free(db, business, lines, start)


def snapshot_chain_is_free(db: Session, business, lines, start, exclude_visit_id=None) -> bool:
    """Use the booked durations and buffers when moving an existing appointment."""
    zone = salon_zone(business.timezone)
    local_start = start.replace(tzinfo=timezone.utc).astimezone(zone)
    local_end = (
        start + timedelta(minutes=total_minutes(lines))
    ).replace(tzinfo=timezone.utc).astimezone(zone)
    first, last, _today = booking_window(
        local_start.date(),
        local_end.date(),
        business.timezone,
        business.booking_horizon_days,
    )
    if (first, last) != (local_start.date(), local_end.date()):
        return False
    schedules, exceptions, blocks, busy = crud_public_booking.availability_sources(
        db, sorted({line.specialist_id for line in lines}), first, last, business.timezone,
        exclude_visit_id=exclude_visit_id,
    )
    cursor = start
    for line in lines:
        if not chain_start_available(
            start=cursor,
            timings=[ChainTiming(line.duration_minutes, line.buffer_before_minutes, line.buffer_after_minutes)],
            timezone_name=business.timezone,
            schedules=schedules, exceptions=exceptions, blocks=blocks, busy=busy,
            staff_id=line.specialist_id,
            lead_time_minutes=business.booking_lead_time_minutes,
        ):
            return False
        cursor += timedelta(minutes=line.duration_minutes)
    return True


def submit_booking(
    db: Session,
    business,
    payload: PublicBookingCreate,
    language: str,
    *,
    commit: bool = True,
) -> Visit:
    from app.api.core.subscription_access import enforce_business_access
    enforce_business_access(db, business.id)
    payments.expire_stale_holds(db, business_id=business.id, commit=commit)
    digest = request_hash(payload)
    existing = db.query(PublicBookingRequest).filter(
        PublicBookingRequest.business_id == business.id,
        PublicBookingRequest.request_id == payload.client_request_id,
    ).first()
    if existing:
        return _replay(db, existing, digest)

    from app.api.core.crm_metering import enforce_visit_quota
    enforce_visit_quota(db, business.id, payload.staff_id, payload.start)
    service_ids = payload.service_ids or [payload.service_id]
    services = [
        crud_public_booking.get_public_service(db, business.id, service_id)
        for service_id in service_ids
    ]
    if any(service is None for service in services):
        raise ServiceUnavailable()
    for service in services:
        if payload.staff_id not in crud_public_booking.public_service_master_ids(
            db, business.id, service
        ):
            raise StaffUnavailable()
    lines = [service_line(service, payload.staff_id) for service in services]

    claim = PublicBookingRequest(
        business_id=business.id,
        request_id=payload.client_request_id,
        request_hash=digest,
        marketing_consent=payload.marketing_consent,
        processing_consent=payload.processing_consent,
    )
    db.add(claim)
    try:
        db.flush()
    except IntegrityError:
        # another request with the same key won the insert; show its result, not a second visit
        db.rollback()
        existing = db.query(PublicBookingRequest).filter(
            PublicBookingRequest.business_id == business.id,
            PublicBookingRequest.request_id == payload.client_request_id,
        ).first()
        if existing:
            return _replay(db, existing, digest)
        raise RequestInProgress()

    staff = (
        db.query(User)
        .filter(
            User.id == payload.staff_id,
            User.deleted_at.is_(None),
            User.archived_at.is_(None),
        )
        .with_for_update()
        .first()
    )
    if not staff:
        db.rollback()
        raise StaffUnavailable()

    start = payload.start
    if start.tzinfo is not None:
        start = start.astimezone(timezone.utc).replace(tzinfo=None)
    if not _chain_is_free(db, business, services, staff.id, start):
        db.rollback()
        raise SlotUnavailable()

    client = crud_public_booking.find_or_create_client(
        db, business.id, staff.id, payload, language
    )
    visit = Visit(
        client_id=client.id,
        specialist_id=staff.id,
        business_id=business.id,
        status="planned",
    )
    apply_chain(visit, lines, start)
    db.add(visit)
    db.flush()

    if payload.marketing_consent:
        # booking itself never opts a guest in; this row exists only because it was ticked
        campaign_service.record_consent(
            db,
            business_id=business.id,
            client_id=client.id,
            source="public_booking",
            source_ref=str(visit.id),
        )

    claim.client_id = client.id
    claim.visit_id = visit.id
    settings, provider = payments.effective_policy(db, business.id)
    attempt = None
    if settings is not None and provider is not None:
        attempt = payments.create_attempt(
            db,
            business,
            visit,
            settings,
            provider,
            request_key=f"{payload.client_request_id}:deposit",
        )
    if attempt is None:
        # a held booking is announced only once the deposit actually clears
        notification_events.queue_booking_created(db, business, visit, client)
    if not commit:
        # the caller keeps its own row locks (waitlist offer) until its transaction ends
        db.flush()
        return visit
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.query(PublicBookingRequest).filter(
            PublicBookingRequest.business_id == business.id,
            PublicBookingRequest.request_id == payload.client_request_id,
        ).first()
        if existing:
            return _replay(db, existing, digest)
        raise RequestInProgress()

    db.refresh(visit)
    try:
        from app.api.core.redis_client import invalidate_client, invalidate_client_list

        invalidate_client(client.id)
        invalidate_client_list()
    except Exception:
        pass
    return visit


def public_booking_out(db: Session, business, visit: Visit, manage_token: str) -> PublicBookingOut:
    staff = visit.specialist
    return PublicBookingOut(
        id=visit.id,
        start=visit.date_start,
        end=visit.date_end or visit.date_start + timedelta(minutes=60),
        service_name=visit.service_name or "",
        staff_id=visit.specialist_id,
        staff_name=staff.full_name if staff else "",
        price=visit.price,
        currency=business.currency,
        status=visit.status or "planned",
        timezone=business.timezone,
        items=[
            PublicBookingItemOut(
                service_id=item.service_id,
                service_name=item.service_name,
                staff_id=item.specialist_id,
                start=item.date_start,
                end=item.date_end,
                price=float(item.price) if item.price is not None else None,
            )
            for item in visit.items
        ],
        payment=payments.payment_out(payments.latest_attempt(db, visit)),
        manage_token=manage_token,
    )
