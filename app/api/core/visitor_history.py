"""Read only explicitly granted visit IDs and times, never CRM ownership or contacts.

The caller authenticates a visitor session before passing account_id. An integer ID is
not authentication. This core neither creates access nor exports financial/public DTOs.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.api.core.clock import utcnow
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.visit import Visit
from app.db.models.visitor_access import VisitorGrant


def _positive_id(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("A positive persisted ID is required")
    return value


@dataclass(frozen=True)
class VisitorHistoryCursor:
    date_start: datetime
    id: int

    def __post_init__(self):
        _positive_id(self.id)
        if not isinstance(self.date_start, datetime):
            raise ValueError("A UTC visit start time is required")
        if self.date_start.tzinfo is not None:
            object.__setattr__(self, "date_start", self.date_start.astimezone(timezone.utc).replace(tzinfo=None))


@dataclass(frozen=True)
class VisitorHistoryEntry:
    id: int
    business_id: int
    date_start: datetime
    date_end: datetime | None
    status: str | None


@dataclass(frozen=True)
class VisitorHistoryPage:
    entries: tuple[VisitorHistoryEntry, ...]
    next_cursor: VisitorHistoryCursor | None


def authorized_visits_query(account_id: int):
    """Build exact granted scope; the caller authenticates the account separately."""
    _positive_id(account_id)
    grant = select(VisitorGrant.id).where(
        VisitorGrant.account_id == account_id,
        VisitorGrant.revoked_at.is_(None),
        VisitorGrant.business_id == Visit.business_id,
        VisitorGrant.client_id == Visit.client_id,
        or_(and_(VisitorGrant.scope == "visit", VisitorGrant.visit_id == Visit.id),
            and_(VisitorGrant.scope == "client", VisitorGrant.visit_id.is_(None))),
    ).correlate(Visit).exists()
    return select(Visit.id, Visit.business_id, Visit.date_start, Visit.date_end, Visit.status).join(
        Business, Business.id == Visit.business_id).join(Client, Client.id == Visit.client_id).where(
        Visit.deleted_at.is_(None), Business.deleted_at.is_(None), Client.deleted_at.is_(None), grant)


def _entry(row) -> VisitorHistoryEntry:
    return VisitorHistoryEntry(row.id, row.business_id, row.date_start, row.date_end, row.status)


def visit_page_query(account_id: int, *, limit: int = 25,
                     cursor: VisitorHistoryCursor | None = None,
                     business_id: int | None = None,
                     category: str = "all", as_of: datetime | None = None):
    """Limit exact grants and categories before item projection. Caller pins as_of across pages."""
    _positive_id(account_id)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("Visit page limit must be between 1 and 100")
    if cursor is not None and not isinstance(cursor, VisitorHistoryCursor):
        raise ValueError("A typed visitor history cursor is required")
    if business_id is not None:
        _positive_id(business_id)
    if not isinstance(category, str) or category not in ("all", "upcoming", "past", "cancelled"):
        raise ValueError("A supported history category is required")
    if as_of is not None:
        if not isinstance(as_of, datetime):
            raise ValueError("A UTC history boundary is required")
        if as_of.tzinfo is not None:
            as_of = as_of.astimezone(timezone.utc).replace(tzinfo=None)
    elif category != "all":
        as_of = utcnow()
    query = authorized_visits_query(account_id)
    if category == "cancelled":
        query = query.where(Visit.status == "cancelled")
    elif category in ("upcoming", "past"):
        remaining = or_(Visit.status.is_(None), Visit.status.notin_(("cancelled", "completed")))
        if category == "upcoming":
            query = query.where(remaining, Visit.date_start >= as_of)
        else:
            query = query.where(or_(Visit.status == "completed", and_(remaining, Visit.date_start < as_of)))
    if business_id is not None:
        query = query.where(Visit.business_id == business_id)
    if cursor is not None:
        query = query.where(or_(Visit.date_start < cursor.date_start,
            and_(Visit.date_start == cursor.date_start, Visit.id < cursor.id)))
    query = query.order_by(Visit.date_start.desc(), Visit.id.desc()).limit(limit + 1)
    return query


def visit_detail_query(account_id: int, visit_id: int):
    """Build one exact authorized visit, without a later unscoped ID lookup."""
    _positive_id(visit_id)
    return authorized_visits_query(account_id).where(Visit.id == visit_id)


def list_visits(db: Session, account_id: int, *, limit: int = 25,
                cursor: VisitorHistoryCursor | None = None,
                business_id: int | None = None,
                category: str = "all", as_of: datetime | None = None) -> VisitorHistoryPage:
    """Return a descending time/ID page from exact active grants, without any writes."""
    query = visit_page_query(account_id, limit=limit, cursor=cursor, business_id=business_id,
                             category=category, as_of=as_of)
    # A reader must not accidentally flush the caller's unrelated pending mutations.
    with db.no_autoflush:
        rows = db.execute(query).all()
    entries = tuple(_entry(row) for row in rows[:limit])
    next_cursor = (VisitorHistoryCursor(entries[-1].date_start, entries[-1].id)
                   if len(rows) > limit else None)
    return VisitorHistoryPage(entries, next_cursor)


def get_visit(db: Session, account_id: int, visit_id: int) -> VisitorHistoryEntry | None:
    """Unknown, foreign and unavailable visits all resolve to None, in one SQL read."""
    _positive_id(account_id)
    _positive_id(visit_id)
    with db.no_autoflush:
        row = db.execute(visit_detail_query(account_id, visit_id)).one_or_none()
    return _entry(row) if row is not None else None
