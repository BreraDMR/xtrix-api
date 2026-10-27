"""One-statement public history, scoped before item joins or scalar projection."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, select
from sqlalchemy.orm import Session, aliased

from app.api.core import visitor_history
from app.api.core.clock import utcnow
from app.db.models.business import Business
from app.db.models.user import User
from app.db.models.visit import Visit
from app.db.models.visit_item import VisitItem
from app.schemas.datetimes import to_naive_utc
from app.schemas.limits import ISO_4217
from app.schemas.visitor_history import (
    VisitorHistoryBusiness, VisitorHistoryCursor, VisitorHistoryItem, VisitorHistoryMoney,
    VisitorHistoryPage, VisitorHistoryVisit,
)


def _price(amount, currency):
    if currency not in ISO_4217 or amount is None:
        return None
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return VisitorHistoryMoney(amount=value, currency=currency) if value.is_finite() else None


def _timezone(value):
    if not isinstance(value, str):
        return None
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return value


def _projection(scoped_query):
    # The same SQL snapshot authorizes, limits and reads public fields. Never reread ORM IDs.
    page = scoped_query.subquery('authorized_history_visits')
    staff = aliased(User, name='history_visit_staff')
    item_staff = aliased(User, name='history_item_staff')
    return select(
        page.c.id.label('visit_id'), page.c.business_id, page.c.date_start, page.c.date_end, page.c.status,
        Visit.service_name, Visit.price.label('visit_price'), Visit.currency_snapshot,
        Visit.client_confirmed_at.label('confirmed_at'),
        Business.name.label('business_name'), Business.address.label('business_address'),
        Business.phone.label('business_phone'), Business.email.label('business_email'),
        Business.timezone.label('business_timezone'), staff.full_name.label('staff_name'),
        VisitItem.id.label('item_id'), VisitItem.position.label('item_position'),
        VisitItem.service_name.label('item_service_name'), VisitItem.price.label('item_price'),
        VisitItem.date_start.label('item_start'), VisitItem.date_end.label('item_end'),
        VisitItem.duration_minutes.label('item_duration'), item_staff.full_name.label('item_staff_name'),
    ).select_from(page).join(Visit, Visit.id == page.c.id).outerjoin(
        Business, Business.id == page.c.business_id).outerjoin(
        staff, and_(staff.id == Visit.specialist_id, staff.deleted_at.is_(None))).outerjoin(
        VisitItem, VisitItem.visit_id == page.c.id).outerjoin(
        item_staff, and_(item_staff.id == VisitItem.specialist_id, item_staff.deleted_at.is_(None))).order_by(
        page.c.date_start.desc(), page.c.id.desc(), VisitItem.position.asc(), VisitItem.id.asc())


def _visits(rows):
    groups = {}
    for row in rows:
        groups.setdefault(row['visit_id'], []).append(row)
    result = []
    for group in groups.values():
        row = group[0]
        items = tuple(VisitorHistoryItem(id=item['item_id'], position=item['item_position'],
            service_name=item['item_service_name'], start=item['item_start'], end=item['item_end'],
            duration_minutes=item['item_duration'], staff_name=item['item_staff_name'],
            price=_price(item['item_price'], row['currency_snapshot']))
            for item in group if item['item_id'] is not None)
        business = VisitorHistoryBusiness(name=row['business_name'], address=row['business_address'],
            phone=row['business_phone'], email=row['business_email'], timezone=_timezone(row['business_timezone']))
        result.append(VisitorHistoryVisit(id=row['visit_id'], business_id=row['business_id'], business=business,
            start=row['date_start'], end=row['date_end'], service_name=row['service_name'], status=row['status'],
            confirmed_at=row['confirmed_at'], staff_name=row['staff_name'],
            price=_price(row['visit_price'], row['currency_snapshot']), items=items))
    return tuple(result)


def list_visits(db: Session, account_id: int, *, limit: int = 25,
                cursor: VisitorHistoryCursor | visitor_history.VisitorHistoryCursor | None = None,
                business_id: int | None = None,
                category: str = "all", as_of: datetime | None = None) -> VisitorHistoryPage:
    """Read one public page. account_id comes from a verified visitor session, not a body.

    Sort keys are stable across equal times, not across rescheduling between pages.
    Unknown currency/timezone remains unknown. No action or payment policy runs here.
    """
    if as_of is not None:
        if not isinstance(as_of, datetime):
            raise ValueError("A UTC history boundary is required")
        as_of = to_naive_utc(as_of)
    if isinstance(cursor, VisitorHistoryCursor):
        if as_of is not None and cursor.as_of is not None and as_of != cursor.as_of:
            raise ValueError("History cursor boundaries must match")
        if as_of is None:
            as_of = cursor.as_of
        cursor = visitor_history.VisitorHistoryCursor(cursor.date_start, cursor.id)
    if category != "all":
        if cursor is not None and as_of is None:
            raise ValueError("Continuing a category requires its original boundary")
        if as_of is None:
            as_of = utcnow()
    scoped = visitor_history.visit_page_query(account_id, limit=limit, cursor=cursor, business_id=business_id,
                                             category=category, as_of=as_of)
    with db.no_autoflush:
        entries = _visits(db.execute(_projection(scoped)).mappings().all())
    selected = entries[:limit]
    next_cursor = (VisitorHistoryCursor(date_start=selected[-1].start, id=selected[-1].id,
                                        as_of=as_of if category != "all" else None)
                   if len(entries) > limit else None)
    return VisitorHistoryPage(entries=selected, next_cursor=next_cursor)


def get_visit(db: Session, account_id: int, visit_id: int) -> VisitorHistoryVisit | None:
    """Unknown, foreign, deleted, revoked and retargeted visits all resolve to None."""
    scoped = visitor_history.visit_detail_query(account_id, visit_id)
    with db.no_autoflush:
        entries = _visits(db.execute(_projection(scoped)).mappings().all())
    return entries[0] if entries else None
