"""Move the stored booking chain without recomputing its sold quote."""
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError

from app.api.core import availability, booking_manage_policy, notification_events, public_booking_service
from app.api.core import visitor_action_target as targets, visitor_reschedule_target as scheduling
from app.api.core.visitor_action_facts import AuthorizedActionTarget, ServiceMasterPair, read_booking_manage_eligibility
from app.api.core.visitor_cancel import _notification_facts
from app.api.core.released_slots import ReleasedSlot,chain_slots
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.business import Business
from app.db.models.visit import Visit
from app.db.models.visit_event import VisitEvent
from app.db.models.visit_item import VisitItem


@dataclass(frozen=True)
class RescheduleResult:
    id: int
    changed: bool
    business_id: int
    service_id: int | None
    specialist_id: int
    start: datetime
    released_slots: tuple[ReleasedSlot,...] = ()


def _allowed(db, raw, locked, original, new_start):
    fresh = scheduling.revalidate_reschedule_target(db, raw, locked)
    eligibility = read_booking_manage_eligibility(db, AuthorizedActionTarget(
        visit_id=fresh.visit_id, business_id=fresh.business_id, status=original.status,
        start=original.date_start, service_master_pairs=tuple(
            ServiceMasterPair(line.service_id, line.specialist_id) for line in locked.lines)), now=fresh.checked_at)
    fresh = scheduling.revalidate_reschedule_target(db, raw, locked)
    if not eligibility.reschedule.allowed:
        raise booking_manage_policy.BookingActionNotAllowed(eligibility.reschedule.reason)
    booking_manage_policy.ensure_reschedule_allowed(original, now=fresh.checked_at)
    if new_start <= fresh.checked_at:
        raise booking_manage_policy.BookingActionNotAllowed('public_slot_unavailable')
    return fresh


def _moved(locked, new_start):
    parent, items = locked.signature
    delta = new_start - locked.authority.date_start
    parent = list(parent)
    parent[7] = new_start
    parent[8] = parent[8] + delta if parent[8] is not None else None
    moved = []
    for values in items:
        row = list(values)
        row[9] += delta
        row[10] += delta
        moved.append(tuple(row))
    return replace(locked, signature=(tuple(parent), tuple(moved)))


def _queue_fresh_change(db, business, visit, person):
    pending = db.new.union(db.dirty).union(db.deleted)
    if any(isinstance(row, NotificationOutbox) and row.visit_id == visit.id for row in pending):
        raise targets.DirtyActionState('Save notification edits before moving a booking')
    # A move back may revive an old reminder. A clean identity map must see the
    # status changed by cancel_pending_reminders in this same transaction.
    try:
        db.execute(select(NotificationOutbox.id).where(NotificationOutbox.visit_id == visit.id,
            NotificationOutbox.kind == notification_events.KIND_REMINDER).order_by(
                NotificationOutbox.id).with_for_update(nowait=True)).all()
        with Session(bind=db.connection(), autoflush=False, join_transaction_mode='rollback_only') as queue:
            notification_events.queue_booking_changed(queue, business, visit, person)
            queue.flush()
    except DBAPIError as error:
        targets._busy_or_raise(error)


def _slot_free(db, business, locked, new_start, visit_id):
    # Schedule and occupancy reads must not inherit the caller's stale identity map.
    with Session(bind=db.connection(), autoflush=False, join_transaction_mode='rollback_only') as reads:
        try:
            return public_booking_service.snapshot_chain_is_free(
                reads, business, locked.lines, new_start, exclude_visit_id=visit_id)
        except OverflowError:
            raise booking_manage_policy.BookingActionNotAllowed('public_slot_unavailable') from None


def _destination_window(business, lines, start, now):
    if start < now + timedelta(minutes=max(0, business.booking_lead_time_minutes)):
        raise booking_manage_policy.BookingActionNotAllowed('public_slot_unavailable')
    zone = availability.salon_zone(business.timezone)
    first = start.replace(tzinfo=timezone.utc).astimezone(zone).date()
    last = (start + timedelta(minutes=sum(line.duration_minutes for line in lines))).replace(
        tzinfo=timezone.utc).astimezone(zone).date()
    allowed_first, allowed_last, _ = availability.booking_window(
        first, last, business.timezone, business.booking_horizon_days, now=now)
    if (first, last) != (allowed_first, allowed_last):
        raise booking_manage_policy.BookingActionNotAllowed('public_slot_unavailable')


def reschedule_visit(db: Session, raw_session: str | None, visit_id: int,
                     new_start: datetime) -> RescheduleResult:
    """Caller commits all changes or rolls back the whole transaction on any error.

    Exact original authority and cutoff are checked after the final queue flush.
    Post-commit slot matching and cache refresh belong to the HTTP caller.
    """
    if (not isinstance(new_start, datetime) or new_start.tzinfo is not None
            or not 2 <= new_start.year <= 9998):
        raise scheduling.RescheduleUnavailable('A normalized UTC start is required')
    with db.no_autoflush:
        locked = scheduling.resolve_reschedule_target(db, raw_session, visit_id, new_start)
        original = locked.authority
        _allowed(db, raw_session, locked, original, new_start)
        service_id, business, visit, person = _notification_facts(db, original)
        result = RescheduleResult(visit_id, new_start != original.date_start,
            original.business_id, service_id, original.specialist_id, original.date_start,
            chain_slots(locked.lines, original.date_start) if new_start != original.date_start else ())
        if not result.changed:
            return result
        window = db.execute(select(Business.booking_horizon_days, Business.booking_lead_time_minutes)
            .where(Business.id == original.business_id)).one()
        business.booking_horizon_days = window.booking_horizon_days
        business.booking_lead_time_minutes = window.booking_lead_time_minutes
        if not _slot_free(db, business, locked, new_start, visit_id):
            raise booking_manage_policy.BookingActionNotAllowed('public_slot_unavailable')
        _allowed(db, raw_session, locked, original, new_start)
        delta = new_start - original.date_start
        moved = _moved(locked, new_start)
        values = {'date_start': new_start, 'date_end': moved.signature[0][8]}
        changed = db.execute(update(Visit).where(Visit.id == visit_id,
            Visit.business_id == original.business_id, Visit.client_id == original.client_id,
            Visit.status == original.status).values(**values).execution_options(synchronize_session=False))
        if changed.rowcount != 1:
            raise targets.ActionTargetUnavailable('Visitor action target is not available')
        db.execute(update(VisitItem).where(VisitItem.visit_id == visit_id).values(
            date_start=VisitItem.date_start + delta, date_end=VisitItem.date_end + delta
        ).execution_options(synchronize_session=False))
        visit.date_start = new_start
        visit.status = original.status
        booking_event = VisitEvent(business_id=original.business_id, visit_id=visit_id,
            event_type='rescheduled', actor='client',
            event_key=f'visit:{visit_id}:rescheduled:{uuid4().hex}', created_at=original.checked_at)
        db.add(booking_event)
        _queue_fresh_change(db, business, visit, person)
        db.flush([booking_event])
        _allowed(db, raw_session, moved, original, new_start)
        if not _slot_free(db, business, locked, new_start, visit_id):
            raise booking_manage_policy.BookingActionNotAllowed('public_slot_unavailable')
        fresh = _allowed(db, raw_session, moved, original, new_start)
        _destination_window(business, locked.lines, new_start, fresh.checked_at)
        return result
