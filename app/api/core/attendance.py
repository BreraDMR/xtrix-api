"""No-shows, late cancels and the per-salon "N no-shows within M months" rule.

A no-show is its own visit status: the client didn't come to a visit that has started.
A late cancel is still a cancelled visit (so slots, reminders and money keep treating it
as cancelled); the flag only says the cancel came inside the salon's window.

Nothing here commits. Callers run it inside their own transaction, so the visit change,
the client tag/block and the journal rows land together or not at all.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.core import audit_service
from app.api.core.clock import utcnow
from app.api.core.logger import get_logger
from app.db.models.business import Business
from app.db.models.client_business_relation import ClientBusinessRelation
from app.db.models.visit import Visit

logger = get_logger("attendance")

NO_SHOW = "no_show"
# a no-show can only come from a visit that was supposed to happen
NO_SHOW_FROM = ("planned", "in_progress")
MAX_TAGS = 20
# a solo specialist has no salon settings; same window as the salon default
DEFAULT_LATE_CANCEL_WINDOW_HOURS = 24


class AttendanceError(ValueError):
    """The requested attendance change doesn't fit the visit; `code` is an i18n key."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def is_late_cancel(window_hours: Optional[int], date_start: Optional[datetime], now: datetime) -> bool:
    """A cancel closer to the start than the window (or after the start) is late."""
    if not window_hours or window_hours <= 0 or date_start is None:
        return False
    return date_start - now < timedelta(hours=window_hours)


def check_no_show_allowed(old_status: Optional[str], date_start: datetime, now: datetime) -> None:
    if (old_status or "planned") not in NO_SHOW_FROM:
        raise AttendanceError("visit_no_show_status")
    if date_start > now:
        raise AttendanceError("visit_no_show_not_started")


def months_ago(now: datetime, months: int) -> datetime:
    # calendar months, clamped to the last day (31 March - 1 month = 28/29 February)
    year, month = divmod(now.year * 12 + (now.month - 1) - months, 12)
    month += 1
    day = min(now.day, calendar.monthrange(year, month)[1])
    return now.replace(year=year, month=month, day=day)


def no_show_count(db: Session, business_id: int, client_id: int, since: Optional[datetime] = None) -> int:
    query = db.query(func.count(Visit.id)).filter(
        Visit.business_id == business_id,
        Visit.client_id == client_id,
        Visit.status == NO_SHOW,
        Visit.deleted_at.is_(None),
    )
    if since is not None:
        query = query.filter(Visit.date_start >= since)
    return int(query.scalar() or 0)


def _relation(db: Session, business_id: int, client_id: int) -> ClientBusinessRelation:
    relation = db.query(ClientBusinessRelation).filter(
        ClientBusinessRelation.business_id == business_id,
        ClientBusinessRelation.client_id == client_id,
    ).with_for_update().first()
    if relation is None:
        relation = ClientBusinessRelation(business_id=business_id, client_id=client_id)
        db.add(relation)
        db.flush()
    return relation


def _with_tag(tags, tag: str) -> Optional[list]:
    """The tag list with `tag` added, or None when nothing changes."""
    current = [str(item) for item in tags] if isinstance(tags, list) else []
    if any(item.casefold() == tag.casefold() for item in current):
        return None
    if len(current) >= MAX_TAGS:
        # the card can't hold more tags; the counter on the card still shows the no-shows
        return None
    return current + [tag]


@dataclass(frozen=True)
class RuleResult:
    no_shows: int
    reached: bool
    tagged: bool = False
    blocked: bool = False
    unblocked: bool = False


def apply_no_show_rule(
    db: Session,
    visit: Visit,
    *,
    actor=None,
    request_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Optional[RuleResult]:
    """Re-check the salon's rule for this visit's client after a no-show was set or undone.

    Reaching the threshold adds the tag and (if the salon wants it) blocks online booking.
    Dropping back under it lifts a block, but leaves the tag: staff may have added it by hand.
    """
    if visit.business_id is None:
        return None
    business = db.get(Business, visit.business_id)
    if business is None or not business.noshow_rule_enabled:
        return None
    now = now or utcnow()
    db.flush()
    count = no_show_count(db, business.id, visit.client_id, months_ago(now, business.noshow_rule_period_months))
    reached = count >= business.noshow_rule_threshold
    relation = _relation(db, business.id, visit.client_id)
    tagged = blocked = unblocked = False
    if reached:
        new_tags = _with_tag(relation.tags, business.noshow_rule_tag)
        if new_tags is not None:
            relation.tags = new_tags
            tagged = True
        if business.noshow_rule_block_online and relation.online_booking_blocked_at is None:
            relation.online_booking_blocked_at = now
            blocked = True
        if tagged or blocked:
            audit_service.record_event(
                db, action="client.no_show_rule_applied", entity_type="client", entity_id=visit.client_id,
                business_id=business.id, actor=actor, request_id=request_id,
                changes={"no_show_count": count, "tag_added": tagged, "online_booking_blocked": blocked},
            )
    elif relation.online_booking_blocked_at is not None and visit.status != NO_SHOW:
        # only an undone no-show lifts the block, a fresh one never does
        relation.online_booking_blocked_at = None
        unblocked = True
        audit_service.record_event(
            db, action="client.online_booking_unblocked", entity_type="client", entity_id=visit.client_id,
            business_id=business.id, actor=actor, request_id=request_id,
            changes={"no_show_count": count},
        )
    return RuleResult(count, reached, tagged, blocked, unblocked)


def set_online_booking_block(
    db: Session, relation: ClientBusinessRelation, blocked: bool, *, actor=None,
    request_id: Optional[str] = None,
) -> bool:
    """Staff switch on the client card. Returns True when something changed."""
    if blocked == (relation.online_booking_blocked_at is not None):
        return False
    relation.online_booking_blocked_at = utcnow() if blocked else None
    audit_service.record_event(
        db, action="client.online_booking_blocked" if blocked else "client.online_booking_unblocked",
        entity_type="client", entity_id=relation.client_id, business_id=relation.business_id,
        actor=actor, request_id=request_id, changes={},
    )
    return True


# What the online booking flow should do for this client. Today the only answer besides
# "go ahead" is a refusal; a future "deposit required" answer would be returned from here
# too and handled by the booking flow next to the refusal.
BOOKING_ALLOWED = None
BOOKING_REFUSED = "online_booking_blocked"


def online_booking_restriction(db: Session, business_id: int, client_id: int) -> Optional[str]:
    """One place that decides whether this client may book online in this salon."""
    blocked_at = db.query(ClientBusinessRelation.online_booking_blocked_at).filter(
        ClientBusinessRelation.business_id == business_id,
        ClientBusinessRelation.client_id == client_id,
    ).scalar()
    return BOOKING_REFUSED if blocked_at is not None else BOOKING_ALLOWED


@dataclass(frozen=True)
class ClientAttendance:
    no_show_count: int = 0
    late_cancel_count: int = 0
    online_booking_blocked: bool = False
    rule_reached: bool = False


def client_attendance(
    db: Session,
    client_ids: list[int],
    *,
    business_id: Optional[int],
    specialist_id: Optional[int] = None,
    now: Optional[datetime] = None,
) -> dict[int, ClientAttendance]:
    """Counters for a page of clients in two queries, whatever the page size."""
    if not client_ids:
        return {}
    from sqlalchemy import case

    query = db.query(
        Visit.client_id,
        func.coalesce(func.sum(case((Visit.status == NO_SHOW, 1), else_=0)), 0),
        func.coalesce(func.sum(case(((Visit.status == "cancelled") & Visit.late_cancel.is_(True), 1), else_=0)), 0),
    ).filter(Visit.client_id.in_(client_ids), Visit.deleted_at.is_(None))
    if business_id:
        query = query.filter(Visit.business_id == business_id)
    elif specialist_id:
        query = query.filter(Visit.specialist_id == specialist_id)
    else:
        query = query.filter(False)
    counts = {row[0]: (int(row[1]), int(row[2])) for row in query.group_by(Visit.client_id).all()}

    blocked: set[int] = set()
    reached: set[int] = set()
    if business_id:
        blocked = {
            row[0] for row in db.query(ClientBusinessRelation.client_id).filter(
                ClientBusinessRelation.business_id == business_id,
                ClientBusinessRelation.client_id.in_(client_ids),
                ClientBusinessRelation.online_booking_blocked_at.isnot(None),
            ).all()
        }
        business = db.get(Business, business_id)
        if business is not None and business.noshow_rule_enabled:
            since = months_ago(now or utcnow(), business.noshow_rule_period_months)
            reached = {
                row[0] for row in db.query(Visit.client_id).filter(
                    Visit.business_id == business_id, Visit.client_id.in_(client_ids),
                    Visit.status == NO_SHOW, Visit.deleted_at.is_(None), Visit.date_start >= since,
                ).group_by(Visit.client_id).having(func.count(Visit.id) >= business.noshow_rule_threshold).all()
            }
    return {
        client_id: ClientAttendance(
            no_show_count=counts.get(client_id, (0, 0))[0],
            late_cancel_count=counts.get(client_id, (0, 0))[1],
            online_booking_blocked=client_id in blocked,
            rule_reached=client_id in reached,
        )
        for client_id in client_ids
    }
