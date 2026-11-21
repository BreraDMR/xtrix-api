"""Guest booking submission.

The order matters: the retry key is claimed in the same transaction as the visit, and
the master's row is locked before the slot is checked again. Two taps on "Book" therefore
produce at most one visit, while a changed body with the same key is rejected.
"""
import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.core.availability import (
    MAX_SLOTS,
    AvailableSlot,
    ChainTiming,
    booking_window,
    chain_start_available,
    collect_slots,
    salon_zone,
)
from app.api.core.clock import utcnow
from app.api.core import campaign_service, notification_events, service_payment_service as payments, visit_currency
from app.api.core.visit_chain import apply_chain, service_line, total_minutes
from app.api.crud import crud_public_booking
from app.db.models.public_booking import PublicBookingRequest
from app.db.models.business import Business
from app.db.models.service import Service
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


def busy_minutes_on_day(busy: dict, staff_id: int, day: date, timezone_name: str) -> int:
    """How many minutes of the salon-local day this master is already booked (buffers included)."""
    zone = salon_zone(timezone_name)
    day_start = datetime.combine(day, time.min).replace(tzinfo=zone).astimezone(timezone.utc).replace(tzinfo=None)
    day_end = datetime.combine(day + timedelta(days=1), time.min).replace(tzinfo=zone).astimezone(
        timezone.utc).replace(tzinfo=None)
    total = timedelta()
    for start, end in busy.get(staff_id, ()):
        left, right = max(start, day_start), min(end, day_end)
        if right > left:
            total += right - left
    return int(total.total_seconds() // 60)


def least_loaded(staff_ids, busy: dict, day: date, timezone_name: str) -> int:
    """The "any master" rule: fewest booked minutes that day, then the lowest id."""
    return min(staff_ids, key=lambda staff_id: (busy_minutes_on_day(busy, staff_id, day, timezone_name), staff_id))


def any_master_slots(per_master: dict, busy: dict, timezone_name: str) -> list[AvailableSlot]:
    """Merge each master's free starts into one list, one entry per start time.

    Each start carries the master the booking would get right now, so the page and the
    booking agree unless someone else books in between (then the booking picks again).
    """
    free: dict[tuple[date, datetime], list[int]] = {}
    for staff_id, slots in per_master.items():
        for slot in slots:
            free.setdefault((slot.day, slot.start), []).append(slot.staff_id)
    merged = [
        AvailableSlot(day=day, start=start, staff_id=least_loaded(ids, busy, day, timezone_name))
        for (day, start), ids in sorted(free.items())
    ]
    return merged[:MAX_SLOTS]


def _pick_free_master(db: Session, business, services, staff_ids: list[int], start) -> int | None:
    """Among the locked candidates, the least loaded one who is still free at ``start``."""
    free = [staff_id for staff_id in staff_ids if _chain_is_free(db, business, services, staff_id, start)]
    if not free:
        return None
    day = start.replace(tzinfo=timezone.utc).astimezone(salon_zone(business.timezone)).date()
    _schedules, _exceptions, _blocks, busy = crud_public_booking.availability_sources(
        db, free, day, day, business.timezone
    )
    return least_loaded(free, busy, day, business.timezone)


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


def _common_masters(db: Session, business_id: int, services) -> set[int]:
    sets = [crud_public_booking.public_service_master_ids(db, business_id, service) for service in services]
    return set.intersection(*sets) if sets else set()


def submit_booking(
    db: Session,
    business,
    payload: PublicBookingCreate,
    language: str,
    *,
    commit: bool = True,
    secondary_phone: str | None = None,
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

    service_ids = payload.service_ids or [payload.service_id]
    services = [
        crud_public_booking.get_public_service(db, business.id, service_id)
        for service_id in service_ids
    ]
    if any(service is None for service in services):
        raise ServiceUnavailable()
    masters = _common_masters(db, business.id, services)
    if payload.staff_id is not None:
        if payload.staff_id not in masters:
            raise StaffUnavailable()
        candidate_ids = [payload.staff_id]
    else:
        # no master chosen: anyone who does this service may get it, decided under the locks below
        candidate_ids = sorted(masters)
        if not candidate_ids:
            raise StaffUnavailable()
    from app.api.core.crm_metering import enforce_visit_quota
    # the quota is counted per salon, so any candidate gives the same answer here
    enforce_visit_quota(db, business.id, candidate_ids[0], payload.start)
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

    # Lock every candidate in id order, so two "any master" bookings can't deadlock each other
    locked = (
        db.query(User)
        .filter(
            User.id.in_(candidate_ids),
            User.deleted_at.is_(None),
            User.archived_at.is_(None),
        )
        .order_by(User.id)
        .with_for_update()
        .all()
    )
    if not locked:
        db.rollback()
        raise StaffUnavailable()

    currency = visit_currency.lock_business_currency(db, business.id)
    business = db.query(Business).filter(Business.id == business.id).populate_existing().one_or_none()
    if business is None or business.deleted_at is not None:
        db.rollback()
        raise ServiceUnavailable()
    # Both objects may have been loaded before the Staff/Business lock wait.
    visit_currency.fresh_catalog(db, service_ids)
    services = [crud_public_booking.get_public_service(db, business.id, service_id) for service_id in service_ids]
    if any(service is None for service in services):
        db.rollback()
        raise ServiceUnavailable()
    masters = _common_masters(db, business.id, services)
    locked = [person for person in locked if person.id in masters]
    if not locked:
        db.rollback()
        raise StaffUnavailable()
    currency = visit_currency.currency_for_catalog(currency, business.id, services)

    start = payload.start
    if start.tzinfo is not None:
        start = start.astimezone(timezone.utc).replace(tzinfo=None)
    if payload.staff_id is not None:
        staff = locked[0]
        if not _chain_is_free(db, business, services, staff.id, start):
            db.rollback()
            raise SlotUnavailable()
    else:
        # checked again now that nobody else can book these masters until we commit
        chosen = _pick_free_master(db, business, services, [person.id for person in locked], start)
        if chosen is None:
            db.rollback()
            raise SlotUnavailable()
        staff = next(person for person in locked if person.id == chosen)
    lines = [service_line(service, staff.id) for service in services]

    client = crud_public_booking.find_or_create_client(
        db, business.id, staff.id, payload, language, secondary_phone=secondary_phone
    )
    visit = Visit(
        client_id=client.id,
        specialist_id=staff.id,
        business_id=business.id,
        currency_snapshot=currency,
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
    except IntegrityError as exc:
        db.rollback()
        constraint = getattr(getattr(exc.orig, 'diag', None), 'constraint_name', None)
        if constraint in {'ex_visits_specialist_slot', 'ex_visit_items_specialist_slot'}:
            raise
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
