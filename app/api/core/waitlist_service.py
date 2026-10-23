"""Waitlist matching, offers and atomic acceptance.

A freed slot is compared with active entries by service, master, salon-local date and
time of day. An offer is a message about a free slot, not a hold: several people may get
one for the same time. Acceptance locks the offer and re-runs the public booking checks,
so exactly one of two racing confirmations can create a visit.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.core import notification_preferences
from app.api.core import campaign_service
from app.api.core.availability import salon_zone
from app.api.core.clock import utcnow
from app.api.core.logger import get_logger
from app.api.core.notification_outbox import enqueue_notification
from app.api.core import public_booking_service as booking_service
from app.api.crud import crud_public_booking, crud_waitlist
from app.db.models.business import Business
from app.db.models.service import Service
from app.db.models.user import User
from app.db.models.visit import Visit
from app.db.models.waitlist import WaitlistEntry, WaitlistOffer
from app.schemas.public_booking import PublicBookingCreate
from app.schemas.waitlist import WaitlistJoinIn

OFFER_TTL = timedelta(hours=2)
MAX_OFFERS_PER_SLOT = 3
logger = get_logger("waitlist")


class ServiceUnavailable(Exception):
    pass


class StaffUnavailable(Exception):
    pass


class OfferUnavailable(Exception):
    pass


class OfferExpired(Exception):
    pass


class OfferConflict(Exception):
    pass


class OfferDeclined(Exception):
    pass


class EntryClosed(Exception):
    pass


def _local(zone, value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(zone)


def time_matches(local_start: datetime, preference: str) -> bool:
    hour = local_start.hour
    if preference == "morning":
        return hour < 12
    if preference == "afternoon":
        return 12 <= hour < 17
    if preference == "evening":
        return hour >= 17
    return True


def join_waitlist(
    db: Session,
    business: Business,
    payload: WaitlistJoinIn,
    locale: str,
) -> tuple[WaitlistEntry, Optional[str], bool]:
    """Create one active entry per identical request; a repeat returns the existing row."""
    from app.api.core.subscription_access import enforce_business_access
    enforce_business_access(db, business.id)
    service = crud_public_booking.get_public_service(db, business.id, payload.service_id)
    if service is None:
        raise ServiceUnavailable()
    master_ids = crud_public_booking.public_service_master_ids(db, business.id, service)
    if payload.staff_id is not None and payload.staff_id not in master_ids:
        raise StaffUnavailable()

    zone = salon_zone(business.timezone)
    today = utcnow().replace(tzinfo=timezone.utc).astimezone(zone).date()
    if payload.preferred_date_from < today:
        raise ServiceUnavailable()

    key = crud_waitlist.dedupe_key(
        business.id,
        str(payload.email),
        payload.service_id,
        payload.staff_id,
        payload.preferred_date_from,
        payload.preferred_date_to,
        payload.time_preference,
    )
    existing = crud_waitlist.find_active_entry(db, business.id, key)
    if existing is not None:
        return existing, None, False

    client = crud_public_booking.find_business_client_by_contact(
        db, business.id, payload.phone, str(payload.email)
    )
    entry = WaitlistEntry(
        business_id=business.id,
        client_id=client.id if client else None,
        service_id=payload.service_id,
        specialist_id=payload.staff_id,
        full_name=payload.full_name,
        email=str(payload.email),
        phone=payload.phone,
        language=locale if locale in ("en", "ru", "cz") else "en",
        preferred_date_from=payload.preferred_date_from,
        preferred_date_to=payload.preferred_date_to,
        time_preference=payload.time_preference,
        marketing_consent=payload.marketing_consent,
        processing_consent=payload.processing_consent,
        status="active",
        dedupe_key=key,
    )
    raw_token = crud_waitlist.issue_entry_token(entry)
    db.add(entry)
    try:
        db.flush()
        if payload.marketing_consent and client is not None:
            campaign_service.record_consent(
                db,
                business_id=business.id,
                client_id=client.id,
                source="waitlist",
                source_ref=str(entry.id),
            )
        db.commit()
    except IntegrityError:
        # another request with the same preferences won; show the same generic answer
        db.rollback()
        existing = crud_waitlist.find_active_entry(db, business.id, key)
        if existing is not None:
            return existing, None, False
        raise
    db.refresh(entry)
    return entry, raw_token, True


def _queue_offer_notification(
    db: Session, business: Business, entry: WaitlistEntry, offer: WaitlistOffer
) -> None:
    channel = notification_preferences.enabled_channel(db, business.id)
    if channel is None:
        return
    enqueue_notification(
        db,
        event_key=f"waitlist_offer:{offer.id}",
        kind="waitlist_offer",
        recipient=entry.email,
        business_id=business.id,
        channel=channel,
        language=entry.language,
        payload={"offer_id": offer.id, "waitlist_entry_id": entry.id},
    )


def _already_offered(db: Session, entry_id: int, event_key: str) -> bool:
    return (
        db.query(WaitlistOffer.id)
        .filter(
            WaitlistOffer.entry_id == entry_id,
            WaitlistOffer.event_key == event_key,
        )
        .first()
        is not None
    )


def _has_pending_offer(db: Session, entry_id: int) -> bool:
    return (
        db.query(WaitlistOffer.id)
        .filter(
            WaitlistOffer.entry_id == entry_id,
            WaitlistOffer.status == "pending",
            WaitlistOffer.expires_at > utcnow(),
        )
        .first()
        is not None
    )


def _offer_candidate(
    db: Session,
    business: Business,
    service: Service,
    specialist_id: int,
    start: datetime,
    now: datetime,
) -> list[WaitlistOffer]:
    zone = salon_zone(business.timezone)
    local_start = _local(zone, start)
    if local_start.date() < utcnow().replace(tzinfo=timezone.utc).astimezone(zone).date():
        return []
    lead_until = now + timedelta(minutes=max(0, business.booking_lead_time_minutes or 0))
    if start < lead_until:
        return []

    entries = (
        db.query(WaitlistEntry)
        .filter(
            WaitlistEntry.business_id == business.id,
            WaitlistEntry.service_id == service.id,
            WaitlistEntry.status == "active",
            WaitlistEntry.preferred_date_from <= local_start.date(),
            or_(
                WaitlistEntry.preferred_date_to.is_(None),
                WaitlistEntry.preferred_date_to >= local_start.date(),
            ),
            or_(
                WaitlistEntry.specialist_id.is_(None),
                WaitlistEntry.specialist_id == specialist_id,
            ),
        )
        .order_by(WaitlistEntry.created_at, WaitlistEntry.id)
        .limit(100)
        .all()
    )

    created: list[WaitlistOffer] = []
    for entry in entries:
        if not time_matches(local_start, entry.time_preference):
            continue
        if _has_pending_offer(db, entry.id):
            continue
        key = f"waitlist:{entry.id}:{specialist_id}:{start.isoformat()}"
        if _already_offered(db, entry.id, key):
            continue
        offer = WaitlistOffer(
            business_id=business.id,
            entry_id=entry.id,
            service_id=service.id,
            specialist_id=specialist_id,
            start=start,
            end=start + timedelta(minutes=max(1, service.duration_minutes or 60)),
            status="pending",
            expires_at=now + OFFER_TTL,
            event_key=key,
        )
        savepoint = db.begin_nested()
        try:
            db.add(offer)
            db.flush()
            savepoint.commit()
        except IntegrityError:
            # another release or an acceptance won the unique pending/event key
            savepoint.rollback()
            continue
        _queue_offer_notification(db, business, entry, offer)
        created.append(offer)
        if len(created) >= MAX_OFFERS_PER_SLOT:
            break
    return created


def release_slot(
    db: Session,
    business: Business,
    service_id: Optional[int],
    specialist_id: Optional[int],
    start: Optional[datetime],
) -> list[WaitlistOffer]:
    """Offer one freed time to the earliest matching active entries."""
    if not service_id or not specialist_id or not start:
        return []
    now = utcnow()
    crud_waitlist.expire_pending_offers(db, now=now, business_id=business.id)
    service = crud_public_booking.get_public_service(db, business.id, service_id)
    if service is None:
        return []
    master_ids = crud_public_booking.public_service_master_ids(db, business.id, service)
    if specialist_id not in master_ids:
        return []
    if notification_preferences.enabled_channel(db, business.id) is None:
        # an offer nobody can receive would only block the entry from a future offer
        return []
    if not booking_service._slot_is_free(db, business, service, specialist_id, start):
        return []
    offers = _offer_candidate(db, business, service, specialist_id, start, now)
    if offers:
        db.commit()
        for offer in offers:
            db.refresh(offer)
    return offers


def release_slot_quietly(
    db: Session,
    business: Optional[Business],
    service_id: Optional[int],
    specialist_id: Optional[int],
    start: Optional[datetime],
) -> None:
    """A released slot must never turn a successful cancellation into a 500."""
    if business is None:
        return
    try:
        release_slot(db, business, service_id, specialist_id, start)
    except Exception as exc:
        db.rollback()
        logger.error(
            "Waitlist matching failed for business_id=%s service_id=%s specialist_id=%s: %s",
            business.id,
            service_id,
            specialist_id,
            type(exc).__name__,
        )


def _offer_rows(db: Session, offer: WaitlistOffer) -> tuple[Business, Service, User]:
    business = db.get(Business, offer.business_id)
    service = db.get(Service, offer.service_id)
    staff = db.get(User, offer.specialist_id)
    if (
        business is None
        or business.deleted_at is not None
        or service is None
        or staff is None
    ):
        raise OfferUnavailable()
    return business, service, staff


def offer_context(db: Session, offer: WaitlistOffer):
    return _offer_rows(db, offer)


def resolve_offer(db: Session, raw_token: Optional[str], lock: bool = False) -> WaitlistOffer:
    offer = crud_waitlist.offer_by_token(db, raw_token, lock=lock)
    if offer is None:
        raise OfferUnavailable()
    from app.api.core.subscription_access import enforce_business_access
    enforce_business_access(db, offer.business_id)
    return offer


def expire_offer(db: Session, offer: WaitlistOffer) -> None:
    now = utcnow()
    offer.status = "expired"
    offer.updated_at = now
    crud_waitlist.cancel_offer_notifications(db, [offer.id], now)
    db.commit()


def _conflict_offer(db: Session, offer: WaitlistOffer) -> None:
    now = utcnow()
    offer.status = "conflict"
    offer.responded_at = now
    offer.updated_at = now
    crud_waitlist.cancel_offer_notifications(db, [offer.id], now)
    db.commit()


def accept_offer(db: Session, raw_token: Optional[str], locale: str) -> Visit:
    offer = resolve_offer(db, raw_token, lock=True)
    entry = db.get(WaitlistEntry, offer.entry_id)
    if entry is None:
        raise OfferUnavailable()
    if offer.status == "accepted":
        visit = db.get(Visit, offer.visit_id) if offer.visit_id else None
        if visit is None or visit.deleted_at is not None:
            raise OfferUnavailable()
        return visit
    if offer.status == "expired":
        raise OfferExpired()
    if offer.status == "conflict":
        raise OfferConflict()
    if offer.status == "declined":
        raise OfferDeclined()
    if offer.status != "pending":
        raise OfferUnavailable()
    if offer.expires_at <= utcnow():
        expire_offer(db, offer)
        raise OfferExpired()
    if entry.status != "active":
        raise EntryClosed()

    business, service, _staff = _offer_rows(db, offer)
    public = crud_public_booking.get_settings(db, business.id)
    if public is None or not public.is_enabled:
        # the salon closed online booking after the offer went out, so no visit from a link.
        # The offer stays pending and simply runs out
        raise OfferUnavailable()
    payload = PublicBookingCreate(
        service_id=offer.service_id,
        staff_id=offer.specialist_id,
        start=offer.start,
        full_name=entry.full_name,
        email=entry.email,
        phone=None,
        marketing_consent=entry.marketing_consent,
        processing_consent=True,
        client_request_id=f"waitlist-{offer.id}",
    )
    try:
        visit = booking_service.submit_booking(db, business, payload, locale, commit=False)
    except booking_service.SlotUnavailable:
        _conflict_offer(db, offer)
        raise OfferConflict()
    except (booking_service.ServiceUnavailable, booking_service.StaffUnavailable):
        _conflict_offer(db, offer)
        raise OfferConflict()
    except (booking_service.RequestIdConflict, booking_service.RequestInProgress):
        _conflict_offer(db, offer)
        raise OfferConflict()

    now = utcnow()
    offer.status = "accepted"
    offer.visit_id = visit.id
    offer.responded_at = now
    offer.updated_at = now
    entry.status = "fulfilled"
    entry.updated_at = now
    if entry.client_id is None:
        entry.client_id = visit.client_id
    db.commit()
    _mark_competing_offers_conflict(db, offer)
    db.refresh(visit)
    try:
        from app.api.core.redis_client import invalidate_client, invalidate_visit

        invalidate_visit(visit.id)
        invalidate_client(visit.client_id)
    except Exception:
        pass
    return visit


def _mark_competing_offers_conflict(db: Session, accepted: WaitlistOffer) -> None:
    """Best effort after the booking commit: never hold this slot's offer rows while
    another acceptance is waiting for the staff lock."""
    try:
        now = utcnow()
        others = (
            db.query(WaitlistOffer)
            .filter(
                WaitlistOffer.business_id == accepted.business_id,
                WaitlistOffer.specialist_id == accepted.specialist_id,
                WaitlistOffer.start == accepted.start,
                WaitlistOffer.status == "pending",
                WaitlistOffer.id != accepted.id,
            )
            .all()
        )
        if not others:
            return
        for other in others:
            other.status = "conflict"
            other.responded_at = now
            other.updated_at = now
        crud_waitlist.cancel_offer_notifications(db, [other.id for other in others], now)
        db.commit()
    except Exception:
        db.rollback()


def decline_offer(db: Session, raw_token: Optional[str]) -> None:
    offer = resolve_offer(db, raw_token, lock=True)
    if offer.status == "declined":
        return
    if offer.status == "accepted":
        raise EntryClosed()
    if offer.status != "pending" or offer.expires_at <= utcnow():
        if offer.status == "pending":
            expire_offer(db, offer)
        raise OfferExpired()
    now = utcnow()
    offer.status = "declined"
    offer.responded_at = now
    offer.updated_at = now
    crud_waitlist.cancel_offer_notifications(db, [offer.id], now)
    db.commit()


def leave_waitlist(db: Session, raw_token: Optional[str]) -> WaitlistEntry:
    entry = crud_waitlist.entry_by_token(db, raw_token, lock=True)
    if entry is None:
        offer = crud_waitlist.offer_by_token(db, raw_token, lock=True)
        if offer is None:
            raise OfferUnavailable()
        if offer.status == "accepted":
            raise EntryClosed()
        entry = (
            db.query(WaitlistEntry)
            .filter(WaitlistEntry.id == offer.entry_id)
            .populate_existing()
            .with_for_update()
            .first()
        )
        if entry is None:
            raise OfferUnavailable()
    from app.api.core.subscription_access import enforce_business_access
    enforce_business_access(db, entry.business_id)
    if entry.status == "fulfilled":
        raise EntryClosed()
    if entry.status == "cancelled":
        return entry

    now = utcnow()
    entry.status = "cancelled"
    entry.cancelled_at = now
    entry.updated_at = now
    offers = (
        db.query(WaitlistOffer)
        .filter(
            WaitlistOffer.entry_id == entry.id,
            WaitlistOffer.status == "pending",
        )
        .all()
    )
    for offer in offers:
        offer.status = "cancelled"
        offer.responded_at = now
        offer.updated_at = now
    crud_waitlist.cancel_offer_notifications(db, [offer.id for offer in offers], now)
    db.commit()
    db.refresh(entry)
    return entry


def cancel_entry(db: Session, entry: WaitlistEntry) -> WaitlistEntry:
    if entry.status in ("cancelled", "fulfilled"):
        return entry
    now = utcnow()
    entry.status = "cancelled"
    entry.cancelled_at = now
    entry.updated_at = now
    offers = (
        db.query(WaitlistOffer)
        .filter(
            WaitlistOffer.entry_id == entry.id,
            WaitlistOffer.status == "pending",
        )
        .all()
    )
    for offer in offers:
        offer.status = "cancelled"
        offer.responded_at = now
        offer.updated_at = now
    crud_waitlist.cancel_offer_notifications(db, [offer.id for offer in offers], now)
    db.commit()
    db.refresh(entry)
    return entry
