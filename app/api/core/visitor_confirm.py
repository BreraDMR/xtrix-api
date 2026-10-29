"""Confirm one explicitly granted visit; the caller owns commit and whole rollback."""
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.api.core import booking_manage_policy, visitor_action_target
from app.api.core.visitor_action_facts import AuthorizedActionTarget, read_booking_manage_eligibility
from app.db.models.visit import Visit


@dataclass(frozen=True)
class ConfirmVisitResult:
    id: int
    changed: bool
    confirmed_at: datetime


def _allowed(db: Session, raw_session: str | None,
             target: visitor_action_target.LockedActionTarget) -> visitor_action_target.LockedActionTarget:
    # Empty pairs skip all catalog/slot work: confirmation keeps the stored booking.
    hint = read_booking_manage_eligibility(db, AuthorizedActionTarget(
        visit_id=target.visit_id, business_id=target.business_id, status=target.status,
        start=target.date_start, service_master_pairs=(),
    ), now=target.checked_at)
    # Billing reads may have waited. Authority and the exact start deadline use a
    # fresh clock after those reads, including after our last DML.
    fresh = visitor_action_target.revalidate_action_target(db, raw_session, target)
    if not hint.confirm.allowed:
        raise booking_manage_policy.BookingActionNotAllowed(hint.confirm.reason)
    booking_manage_policy.ensure_confirmation_allowed(fresh, now=fresh.checked_at)
    return fresh


def confirm_visit(db: Session, raw_session: str | None, visit_id: int) -> ConfirmVisitResult:
    """Set only client_confirmed_at once, without flushing unrelated pending objects.

    Every call, including an already-confirmed no-op, resolves current exact visitor
    authority and checks salon write access/status/start time. Locks come only from
    the shared resolver; this core does not commit, roll back or open savepoints.
    The caller must whole-roll back on any exception, including a late guard or a
    failed commit. A returned result is provisional until that caller commit.

    Business billing is read again after our scoped UPDATE; this is a final policy
    check, not strict serialization with every subscription writer until commit.
    No event, notification, payment, item, client or catalog mutation is performed.
    """
    with db.no_autoflush:
        target = visitor_action_target.resolve_action_target(db, raw_session, visit_id)
        target = _allowed(db, raw_session, target)
        if target.client_confirmed_at is not None:
            return ConfirmVisitResult(target.visit_id, False, target.client_confirmed_at)
        changed = db.execute(update(Visit).where(
            Visit.id == target.visit_id, Visit.business_id == target.business_id,
            Visit.client_id == target.client_id, Visit.client_confirmed_at.is_(None),
        ).values(client_confirmed_at=target.checked_at).execution_options(synchronize_session=False))
        if changed.rowcount != 1:
            raise visitor_action_target.ActionTargetUnavailable("Visitor action target is not available")
        fresh = _allowed(db, raw_session, target)
        if fresh.client_confirmed_at is None:
            raise visitor_action_target.ActionTargetUnavailable("Visitor action target is not available")
        return ConfirmVisitResult(fresh.visit_id, True, fresh.client_confirmed_at)
