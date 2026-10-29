"""Atomic visitor cancellation; delivery and released-slot matching are post-commit work."""
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.core import booking_manage_policy, notification_events, visitor_action_target as targets
from app.api.core.visitor_action_facts import AuthorizedActionTarget, read_booking_manage_eligibility
from app.api.core.visitor_pending_payments import cancel_pending_payments
from app.api.core.visit_event_keys import cancellation_event_key
from app.api.core.released_slots import ReleasedSlot,cancellation_slots
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.notification_settings import NotificationSettings
from app.db.models.visit import Visit
from app.db.models.visit_event import VisitEvent


@dataclass(frozen=True)
class CancelVisitResult:
    id: int
    changed: bool
    business_id: int
    service_id: int | None
    specialist_id: int
    start: datetime
    released_slots: tuple[ReleasedSlot,...] = ()


def _allowed(db, raw, original, *, after_change=False):
    hint = read_booking_manage_eligibility(db, AuthorizedActionTarget(
        visit_id=original.visit_id, business_id=original.business_id, status=original.status,
        start=original.date_start, service_master_pairs=(),
    ), now=original.checked_at)
    fresh = targets.revalidate_action_target(db, raw, original)
    if hint.cancel.reason == 'visitor_actions_unavailable':
        raise booking_manage_policy.BookingActionNotAllowed(hint.cancel.reason)
    if after_change:
        if fresh.status != 'cancelled':
            raise targets.ActionTargetUnavailable('Visitor action target is not available')
        # Our own status change must not bypass the original booking's cutoff.
        booking_manage_policy.ensure_cancellation_allowed(original, now=fresh.checked_at)
    elif fresh.status != 'cancelled':
        booking_manage_policy.ensure_cancellation_allowed(fresh, now=fresh.checked_at)
    return fresh


def _notification_facts(db, target):
    # Use current scalars under the existing anchors, never stale caller ORM contacts.
    if any(isinstance(row, NotificationSettings) for row in db.new.union(db.dirty).union(db.deleted)):
        raise targets.DirtyActionState('Save notification preference edits before cancelling a booking')
    # The shared producer reads ORM settings; refresh only after rejecting pending edits.
    db.query(NotificationSettings).filter(NotificationSettings.business_id == target.business_id
                                          ).populate_existing().all()
    row = db.execute(select(
        Visit.service_id, Visit.service_name, Business.name.label('business_name'),
        Business.timezone.label('business_timezone'), Client.full_name,
        Client.contact_email, Client.language,
    ).select_from(Visit).join(Business, Business.id == Visit.business_id).join(
        Client, Client.id == Visit.client_id).where(
            Visit.id == target.visit_id, Visit.business_id == target.business_id,
            Visit.client_id == target.client_id, Visit.deleted_at.is_(None),
            Business.deleted_at.is_(None), Client.deleted_at.is_(None))).one_or_none()
    if row is None:
        raise targets.ActionTargetUnavailable('Visitor action target is not available')
    business = SimpleNamespace(id=target.business_id, name=row.business_name, timezone=row.business_timezone)
    visit = SimpleNamespace(id=target.visit_id, service_name=row.service_name, date_start=target.date_start)
    person = SimpleNamespace(full_name=row.full_name, contact_email=row.contact_email, language=row.language)
    return row.service_id, business, visit, person


def cancel_visit(db: Session, raw_session: str | None, visit_id: int) -> CancelVisitResult:
    """Cancel exactly the granted booking and enqueue its event in one transaction.

    Every failure requires whole caller rollback; nothing here commits or contacts
    a provider. Pending attempts use NOWAIT, paid/refunded amounts stay unchanged.
    Only the caller may match a released slot after a successful changed commit.
    Billing is rechecked after the final flush, without claiming serialization
    against all subscription writers until commit or the HTTP response.
    """
    with db.no_autoflush:
        original = targets.resolve_action_target(db, raw_session, visit_id)
        original = _allowed(db, raw_session, original)
        service_id, business, visit, person = _notification_facts(db, original)
        if original.status == 'cancelled':
            return CancelVisitResult(visit_id, False, original.business_id, service_id,
                                     original.specialist_id, original.date_start)
        released = cancellation_slots(db, original, service_id)
        cancel_pending_payments(db, raw_session, original)
        original = _allowed(db, raw_session, original)
        changed = db.execute(update(Visit).where(
            Visit.id == original.visit_id, Visit.business_id == original.business_id,
            Visit.client_id == original.client_id, Visit.status == original.status,
        ).values(status='cancelled').execution_options(synchronize_session=False))
        if changed.rowcount != 1:
            raise targets.ActionTargetUnavailable('Visitor action target is not available')
        event_key = cancellation_event_key(visit_id)
        event = VisitEvent(business_id=original.business_id, visit_id=visit_id,
            event_type='cancelled', actor='client', event_key=event_key,
            created_at=original.checked_at)
        db.add(event)
        notification_events.queue_booking_cancelled(db, business, visit, person, event_key=event_key)
        db.flush([event])
        _allowed(db, raw_session, original, after_change=True)
        return CancelVisitResult(visit_id, True, original.business_id, service_id,
                                 original.specialist_id, original.date_start, released)
