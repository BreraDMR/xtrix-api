from dataclasses import replace
from datetime import timedelta
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional, Tuple
from sqlalchemy import and_, or_, func, select
from sqlalchemy.orm import Session, joinedload
from app.api.core.pagination import PaginationParams
from app.api.core.soft_delete import filter_active
from app.api.core.visit_chain import (
    ChainError,
    ChainLine,
    apply_chain,
    item_intervals,
    legacy_line,
    lines_from_items,
    money,
    service_line,
    total_price,
)
from app.api.core import audit_service, visit_currency, visit_settlement_guard
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.service import Service
from app.db.models.visit import Visit
from app.schemas.visit import VisitCreate, VisitUpdate
from app.schemas.limits import ISO_4217

class CompletedVisitPaymentRequired(ValueError):
    """The effective completed visit needs an existing payment method."""


VISIT_AUDIT_VALUE_FIELDS = (
    "status",
    "date_start",
    "date_end",
    "service_id",
    "specialist_id",
    "price",
    "payment_method",
)
VISIT_AUDIT_NAME_FIELDS = VISIT_AUDIT_VALUE_FIELDS + (
    "service_name",
    "note",
    "photo_url",
)


def _audit_equal(old: Any, new: Any) -> bool:
    if old is None or new is None:
        return old is None and new is None
    if isinstance(old, (Decimal, float, int)) or isinstance(new, (Decimal, float, int)):
        try:
            return Decimal(str(old)) == Decimal(str(new))
        except Exception:
            return old == new
    return old == new


def _item_signature(visit: Visit) -> tuple:
    return tuple(
        (
            item.service_id,
            item.specialist_id,
            item.date_start,
            item.date_end,
            str(item.price) if item.price is not None else None,
            item.duration_minutes,
        )
        for item in sorted(visit.items, key=lambda row: row.position)
    )


def existing_lines(visit: Visit) -> list[ChainLine]:
    if visit.items:
        return [
            ChainLine(
                service_id=item.service_id,
                specialist_id=item.specialist_id,
                service_name=item.service_name,
                price=money(item.price),
                duration_minutes=max(1, item.duration_minutes or 1),
                buffer_before_minutes=max(0, item.buffer_before_minutes or 0),
                buffer_after_minutes=max(0, item.buffer_after_minutes or 0),
            )
            for item in sorted(visit.items, key=lambda row: row.position)
        ]
    return [
        legacy_line(
            visit.service,
            visit.specialist_id,
            visit.service_name,
            visit.price,
            visit.date_start,
            visit.date_end,
        )
    ]


def _business_for_visit(db: Session, specialist_id: int, business_id: Optional[int]) -> Optional[int]:
    if business_id is not None:
        return business_id
    from app.db.models.user import User

    specialist = db.query(User).filter(User.id == specialist_id).first()
    return specialist.business_id if specialist is not None else None


def assert_no_conflict(db: Session, intervals, exclude_id: Optional[int] = None) -> None:
    for specialist_id, start, end in intervals:
        if specialist_id is None:
            continue
        if find_overlapping_visit(db, specialist_id, start, end, exclude_id):
            raise ChainError("time_slot_taken", "This time is already booked")


def prospective_update_lines(db: Session, visit: Visit, update: VisitUpdate) -> list[ChainLine]:
    """The chain an update would save, without writing it (also used for the clash check)."""
    data = update.model_dump(exclude_unset=True)
    lines = existing_lines(visit)
    if update.items is not None:
        requested = [(item.service_id, item.specialist_id or visit.specialist_id)
                     for item in update.items]
        stored = [(line.service_id, line.specialist_id) for line in lines]
        if requested == stored:
            return lines
        return lines_from_items(db, update.items, visit.specialist_id)
    new_service_id = data.get("service_id")
    if new_service_id is not None and new_service_id != visit.service_id:
        if len(lines) > 1:
            raise ChainError(
                "items_required",
                "A multi-service visit can only change its services through items",
            )
        service = db.query(Service).filter(Service.id == new_service_id).first()
        if service is None:
            raise ChainError("service_not_found", "The selected service was not found")
        lines = [service_line(service, visit.specialist_id)]
    if "price" in data and data["price"] is not None and len(lines) == 1:
        lines = [replace(lines[0], price=money(data["price"]))]
    if "date_end" in data and data["date_end"] is not None and len(lines) == 1:
        start = data.get("date_start") or visit.date_start
        duration = max(1, int(round((data["date_end"] - start).total_seconds() / 60)))
        lines = [replace(lines[0], duration_minutes=duration)]
    return lines


def create_visit(
    db: Session,
    visit_in: VisitCreate,
    business_id: Optional[int] = None,
    *,
    actor=None,
    request_id: Optional[str] = None,
) -> Visit:
    """Create the visit and every item in one transaction: no half-saved chains."""
    from app.api.core.logger import get_logger

    logger = get_logger("visits")
    data = visit_in.model_dump(exclude={"used_products", "items"})
    resolved_business_id = _business_for_visit(db, data["specialist_id"], business_id)

    from app.api.core.crm_metering import enforce_visit_quota
    enforce_visit_quota(db, resolved_business_id, data["specialist_id"], data["date_start"], data.get("status") or "planned")
    user_ids = [data["specialist_id"], getattr(actor, "id", None)]
    if visit_in.items is not None:
        user_ids.extend(item.specialist_id or data["specialist_id"] for item in visit_in.items)
    visit_currency.lock_user_references(db, user_ids)
    currency = visit_currency.lock_business_currency(db, resolved_business_id)
    service_ids = ({item.service_id for item in visit_in.items} if visit_in.items is not None
                   else {data["service_id"]} if data.get("service_id") else set())
    catalog = visit_currency.fresh_catalog(db, service_ids)
    service = catalog.get(data.get("service_id"))
    currency = (visit_currency.currency_for_catalog(currency, resolved_business_id, catalog.values())
                if service_ids.issubset(catalog) else None)
    if visit_in.items is not None:
        lines = lines_from_items(db, visit_in.items, data["specialist_id"])
    else:
        price = data.get("price")
        if service is not None and (price is None or price == 0) and service.price is not None:
            price = service.price
        lines = [
            legacy_line(
                service,
                data["specialist_id"],
                data.get("service_name"),
                price,
                data["date_start"],
                data.get("date_end"),
            )
        ]

    assert_no_conflict(db, item_intervals(data["date_start"], lines))
    visit = Visit(
        client_id=data["client_id"],
        specialist_id=data["specialist_id"],
        service_id=data.get("service_id"),
        business_id=resolved_business_id,
        currency_snapshot=currency,
        date_start=data["date_start"],
        date_end=data.get("date_end"),
        service_name=data.get("service_name"),
        note=data.get("note"),
        photo_url=data.get("photo_url"),
        price=data.get("price"),
        status=data.get("status") or "planned",
        payment_method=data.get("payment_method"),
    )
    apply_chain(visit, lines, data["date_start"])
    try:
        db.add(visit)
        db.flush()
        audit_service.record_event(
            db,
            action="visit.created",
            entity_type="visit",
            entity_id=visit.id,
            business_id=visit.business_id,
            owner_user_id=visit.specialist_id if visit.business_id is None else None,
            changes={
                "fields": list(VISIT_AUDIT_NAME_FIELDS) + ["items"],
                "status": visit.status,
                "date_start": visit.date_start,
                "date_end": visit.date_end,
                "service_id": visit.service_id,
                "specialist_id": visit.specialist_id,
                "price": visit.price,
                "payment_method": visit.payment_method,
                "items_count": len(visit.items),
            },
            actor=actor,
            request_id=request_id,
        )
        db.commit()
        db.refresh(visit)
        logger.info(f"create_visit: created visit id={visit.id}, items={len(visit.items)}, price={visit.price}")
        return visit
    except Exception as exc:
        logger.error(f"create_visit: error creating visit: {str(exc)}", exc_info=True)
        db.rollback()
        raise


def update_visit(
    db: Session,
    visit: Visit,
    update: VisitUpdate,
    *,
    actor=None,
    request_id: Optional[str] = None,
) -> Visit:
    """Patch the visit. A chain is rebuilt only when its shape, time or price changes."""
    data = update.model_dump(exclude_unset=True)
    from app.api.core.crm_metering import enforce_visit_quota
    try:
        with db.no_autoflush:
            enforce_visit_quota(db, visit.business_id, visit.specialist_id,
                data.get("date_start") or visit.date_start, data.get("status", visit.status), exclude_id=visit.id)
            proposed_masters = [item.specialist_id or visit.specialist_id for item in (update.items or [])]
            proposed_masters.append(data.get('specialist_id'))
            visit = visit_settlement_guard.lock_quote_update_target(
                db, visit, proposed_masters, getattr(actor, 'id', None))
            previous = (visit.status, visit.date_start, visit.service_id, visit.specialist_id, visit.business_id)
            from app.api.core import released_slots
            before_slots = released_slots.visit_slots(visit)
            structural = any(key in data for key in ("items", "service_id", "date_start", "date_end", "price"))
            before = {field: getattr(visit, field) for field in VISIT_AUDIT_NAME_FIELDS}
            before_items = _item_signature(visit)
            owner_user_id = visit.specialist_id
            if structural:
                catalog_ids = [item.service_id for item in (update.items or [])]
                if data.get('service_id'):
                    catalog_ids.append(data['service_id'])
                visit_currency.fresh_catalog(db, catalog_ids)
                stored = existing_lines(visit)
                lines = prospective_update_lines(db, visit, update)
                same_chain = [(line.service_id, line.specialist_id) for line in lines] == [
                    (line.service_id, line.specialist_id) for line in stored]
                explicit_price = (update.items is None and data.get("price") is not None and len(lines) == 1)
                prospective_total = before['price'] if same_chain and not explicit_price else total_price(lines)
                quote_changed = (not _audit_equal(before['price'], prospective_total)
                    or [(line.service_id, line.specialist_id, line.price) for line in stored] != [
                        (line.service_id, line.specialist_id, line.price) for line in lines])
                if quote_changed:
                    visit_settlement_guard.ensure_price_change_allowed(db, visit.id, visit.business_id)
                if not same_chain and visit.business_id is not None and visit.currency_snapshot in ISO_4217:
                    # The business anchor is already held. Read the unit as a scalar,
                    # not from a caller's cached model, before copying catalog prices.
                    catalog_currency = db.execute(select(Business.currency).where(
                        Business.id == visit.business_id)).scalar_one_or_none()
                    if visit.currency_snapshot != catalog_currency:
                        raise visit_settlement_guard.VisitPriceChangeBlocked('visit_price_currency_explicit_required')
                start = data.get("date_start") or visit.date_start
                assert_no_conflict(db, item_intervals(start, lines), exclude_id=visit.id)
                apply_chain(visit, lines, start)
                if same_chain and not explicit_price:
                    visit.price = before["price"]

            computed = {"items", "service_id", "date_start", "date_end", "price"}
            for field, value in data.items():
                if field in computed:
                    continue
                setattr(visit, field, value)

            if (visit.status == "completed" and visit.price and visit.price > 0
                    and visit.payment_method not in {"card", "cash", "other"}):
                raise CompletedVisitPaymentRequired("Payment method is required for this completed visit")

            changes: dict[str, Any] = {}
            changed_fields = []
            for field in VISIT_AUDIT_NAME_FIELDS:
                new_value = getattr(visit, field)
                if _audit_equal(before[field], new_value):
                    continue
                changed_fields.append(field)
                if field in VISIT_AUDIT_VALUE_FIELDS:
                    changes[field] = {"old": before[field], "new": new_value}

            after_items = _item_signature(visit)
            if "items" in data and after_items != before_items:
                changed_fields.append("items")
                changes["items_count"] = {"old": len(before_items), "new": len(after_items)}

            if changed_fields:
                changes["fields"] = changed_fields
                if before["status"] != "cancelled" and visit.status == "cancelled":
                    action = "visit.cancelled"
                elif (
                    "date_start" in changed_fields or "date_end" in changed_fields
                ):
                    action = "visit.moved"
                else:
                    action = "visit.updated"
                audit_service.record_event(
                    db,
                    action=action,
                    entity_type="visit",
                    entity_id=visit.id,
                    business_id=visit.business_id,
                    owner_user_id=owner_user_id if visit.business_id is None else None,
                    changes=changes,
                    actor=actor,
                    request_id=request_id,
                )
        released = released_slots.changed_slots(before_slots, released_slots.visit_slots(visit), previous[0], visit.status)
        db.add(visit)
        db.commit()
        db.refresh(visit)
        visit._released_slots = released
        visit._updated_from = previous
        return visit
    except Exception:
        db.rollback()
        raise

def _no_business_but_ours(business_id: int):
    # visits with no business are a solo specialist's - only count them when that specialist
    # is in this business now (e.g. the owner's own visits from before the business existed)
    from sqlalchemy import and_, select
    from app.db.models.user import User
    return and_(
        Visit.business_id.is_(None),
        Visit.specialist_id.in_(select(User.id).where(User.business_id == business_id)),
    )
def get_visit(
    db: Session, 
    visit_id: int, 
    include_deleted: bool = False,
    business_id: Optional[int] = None  
) -> Optional[Visit]:
    query = db.query(Visit).options(
        joinedload(Visit.client),
        joinedload(Visit.specialist),
        joinedload(Visit.service),
    )
    if not include_deleted:
        query = filter_active(query)
    query = query.filter(Visit.id == visit_id)
    if business_id:
        from sqlalchemy import inspect
        inspector = inspect(db.bind)
        has_business_id_column = False
        try:
            columns = [col['name'] for col in inspector.get_columns('visits')]
            has_business_id_column = 'business_id' in columns
        except:
            pass
        if has_business_id_column:
            from sqlalchemy import or_
            query = query.filter(or_(
                Visit.business_id == business_id,
                _no_business_but_ours(business_id),
            ))
    return query.first()
def _directory_sql(search, status, sort_by, sort_order):
    conditions = ""
    params = {}
    if search:
        value = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params["directory_search"] = f"%{value}%"
        conditions += """ AND (COALESCE(service_name, (SELECT s.name FROM services s WHERE s.id = visits.service_id)) ILIKE :directory_search
            OR EXISTS (SELECT 1 FROM clients c WHERE c.id = visits.client_id AND c.full_name ILIKE :directory_search))"""
    if status:
        conditions += " AND status = :directory_status"
        params["directory_status"] = status
    columns = {key: key for key in ("id", "date_start", "status", "price")}
    columns["service_name"] = "LOWER(COALESCE(service_name, (SELECT s.name FROM services s WHERE s.id = visits.service_id)))"
    columns["client_name"] = "(SELECT LOWER(c.full_name) FROM clients c WHERE c.id = visits.client_id)"
    direction = "DESC" if sort_order == "desc" else "ASC"
    order = f" ORDER BY {columns.get(sort_by, 'id')} {direction} NULLS LAST, id ASC"
    return conditions, params, order


def list_visits(
    db: Session,
    client_id: Optional[int] = None,
    specialist_id: Optional[int] = None,
    business_id: Optional[int] = None,  
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    pagination: Optional[PaginationParams] = None,
    include_deleted: bool = False,
    include_archived: bool = False,
    search: Optional[str] = None,
    status: Optional[str] = None,
    sort_by: str = "id",
    sort_order: str = "asc",
    calendar_overlap: bool = False,
) -> Tuple[list[Visit], int]:
    directory_sql, directory_params, directory_order = _directory_sql(search, status, sort_by, sort_order)
    from sqlalchemy import inspect, text
    # Share the predicate with legacy-schema count/select fallbacks.
    range_sql = ""
    range_params = {}
    if date_from is not None:
        end = ("CASE WHEN date_end > date_start THEN date_end "
               "ELSE date_start + interval '1 hour' END")
        range_sql += f" AND ({end}) > :date_from" if calendar_overlap else " AND date_start >= :date_from"
        range_params['date_from'] = date_from
    if date_to is not None:
        range_sql += " AND date_start < :date_to" if calendar_overlap else " AND date_start <= :date_to"
        range_params['date_to'] = date_to
    inspector = inspect(db.bind)
    has_business_id_column = False
    try:
        columns = [col['name'] for col in inspector.get_columns('visits')]
        has_business_id_column = 'business_id' in columns
    except:
        pass
    if not has_business_id_column:
        count_sql = "SELECT COUNT(*) FROM visits WHERE 1=1"
        params = {}
        if not include_deleted:
            count_sql += " AND deleted_at IS NULL"
        if not include_archived:
            count_sql += " AND archived_at IS NULL"
        if client_id:
            count_sql += " AND client_id = :client_id"
            params['client_id'] = client_id
        if specialist_id:
            count_sql += " AND specialist_id = :specialist_id"
            params['specialist_id'] = specialist_id
        count_sql += range_sql
        params.update(range_params)
        count_sql += directory_sql
        params.update(directory_params)
        total = db.execute(text(count_sql), params).scalar()
        select_sql = """
            SELECT id, client_id, specialist_id, service_id, date_start, date_end, 
                   service_name, note, photo_url, price, status, created_at, 
                   deleted_at, archived_at
            FROM visits
            WHERE 1=1
        """
        select_params = {}
        if not include_deleted:
            select_sql += " AND deleted_at IS NULL"
        if not include_archived:
            select_sql += " AND archived_at IS NULL"
        if client_id:
            select_sql += " AND client_id = :client_id"
            select_params['client_id'] = client_id
        if specialist_id:
            select_sql += " AND specialist_id = :specialist_id"
            select_params['specialist_id'] = specialist_id
        select_sql += range_sql
        select_params.update(range_params)
        select_sql += directory_sql + directory_order
        select_params.update(directory_params)
        if pagination:
            select_sql += " LIMIT :limit OFFSET :offset"
            select_params['limit'] = pagination.limit
            select_params['offset'] = pagination.offset
        result = db.execute(text(select_sql), select_params)
        items = []
        services_has_business_id = False
        try:
            service_columns = [col['name'] for col in inspector.get_columns('services')]
            services_has_business_id = 'business_id' in service_columns
        except:
            pass
        for row in result:
            visit = Visit()
            for key, value in row._mapping.items():
                setattr(visit, key, value)
            if visit.client_id:
                visit.client = db.query(Client).filter(Client.id == visit.client_id).first()
            if visit.specialist_id:
                from app.db.models.user import User
                visit.specialist = db.query(User).filter(User.id == visit.specialist_id).first()
            if visit.service_id:
                if not services_has_business_id:
                    service_row = db.execute(
                        text("SELECT id, specialist_id, name, duration_minutes, price, is_active, created_at, deleted_at, archived_at FROM services WHERE id = :id"),
                        {"id": visit.service_id}
                    ).first()
                    if service_row:
                        service = Service()
                        for key, value in service_row._mapping.items():
                            setattr(service, key, value)
                        visit.service = service
                else:
                    visit.service = db.query(Service).filter(Service.id == visit.service_id).first()
            items.append(visit)
        return items, total
    q = db.query(Visit).options(
        joinedload(Visit.client),
        joinedload(Visit.specialist),
        joinedload(Visit.service),
    )
    if not include_deleted:
        q = filter_active(q)
    if not include_archived:
        q = q.filter(Visit.archived_at.is_(None))
    if business_id and has_business_id_column:
        q = q.filter(or_(
            Visit.business_id == business_id,
            _no_business_but_ours(business_id),
        ))
    if client_id:
        q = q.filter(Visit.client_id == client_id)
    if specialist_id:
        q = q.filter(Visit.specialist_id == specialist_id)
    if range_sql:
        q = q.filter(text(range_sql[5:])).params(**range_params)
    if search:
        escaped = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        q = q.filter(or_(
            func.coalesce(Visit.service_name, select(Service.name).where(Service.id == Visit.service_id).scalar_subquery()).ilike(pattern, escape="\\"),
            Visit.client.has(Client.full_name.ilike(pattern, escape="\\")),
        ))
    if status:
        q = q.filter(Visit.status == status)
    columns = {key: getattr(Visit, key) for key in ("id", "date_start", "status", "price")}
    columns["service_name"] = func.lower(func.coalesce(Visit.service_name, select(Service.name).where(Service.id == Visit.service_id).scalar_subquery()))
    columns["client_name"] = select(func.lower(Client.full_name)).where(Client.id == Visit.client_id).scalar_subquery()
    column = columns.get(sort_by, Visit.id)
    order = column.desc() if sort_order == "desc" else column.asc()
    try:
        total = q.count()
    except Exception as e:
        if 'business_id' in str(e).lower() or 'does not exist' in str(e).lower():
            from sqlalchemy import text
            count_sql = "SELECT COUNT(*) FROM visits WHERE 1=1"
            params = {}
            if not include_deleted:
                count_sql += " AND deleted_at IS NULL"
            if not include_archived:
                count_sql += " AND archived_at IS NULL"
            if client_id:
                count_sql += " AND client_id = :client_id"
                params['client_id'] = client_id
            if specialist_id:
                count_sql += " AND specialist_id = :specialist_id"
                params['specialist_id'] = specialist_id
            count_sql += range_sql
            params.update(range_params)
            count_sql += directory_sql
            params.update(directory_params)
            total = db.execute(text(count_sql), params).scalar()
        else:
            raise
    q = q.order_by(order.nulls_last(), Visit.id.asc())
    if pagination:
        q = q.offset(pagination.offset).limit(pagination.limit)
    try:
        items = q.all()
    except Exception as e:
        if 'business_id' in str(e).lower() or 'does not exist' in str(e).lower():
            from sqlalchemy import text
            select_sql = """
                SELECT id, client_id, specialist_id, service_id, date_start, date_end, 
                       service_name, note, photo_url, price, status, created_at, 
                       deleted_at, archived_at
                FROM visits
                WHERE 1=1
            """
            params = {}
            if not include_deleted:
                select_sql += " AND deleted_at IS NULL"
            if not include_archived:
                select_sql += " AND archived_at IS NULL"
            if client_id:
                select_sql += " AND client_id = :client_id"
                params['client_id'] = client_id
            if specialist_id:
                select_sql += " AND specialist_id = :specialist_id"
                params['specialist_id'] = specialist_id
            select_sql += range_sql
            params.update(range_params)
            select_sql += directory_sql + directory_order
            params.update(directory_params)
            if pagination:
                select_sql += " LIMIT :limit OFFSET :offset"
                params['limit'] = pagination.limit
                params['offset'] = pagination.offset
            result = db.execute(text(select_sql), params)
            items = []
            for row in result:
                visit = Visit()
                for key, value in row._mapping.items():
                    setattr(visit, key, value)
                items.append(visit)
        else:
            raise
    return items, total
def find_overlapping_visit(db: Session, specialist_id: int, start, end, exclude_id: Optional[int] = None):
    """A specialist can't be in two places at once.

    Production visits are represented by their items, so a master change inside a chain is
    checked against that master's own interval. Rows without items (legacy or direct DB
    writes) still fall back to the visit-level range.
    """
    from app.db.models.visit_item import VisitItem

    item_query = (
        db.query(VisitItem)
        .join(Visit, VisitItem.visit_id == Visit.id)
        .filter(
            VisitItem.specialist_id == specialist_id,
            Visit.deleted_at.is_(None),
            or_(Visit.status.is_(None), Visit.status != "cancelled"),
            # widen by a day in SQL; buffers are applied exactly in Python
            VisitItem.date_start < end + timedelta(days=1),
            VisitItem.date_end > start - timedelta(days=1),
        )
    )
    if exclude_id:
        item_query = item_query.filter(Visit.id != exclude_id)
    for item in item_query.all():
        occupied_start = item.date_start - timedelta(minutes=max(0, item.buffer_before_minutes or 0))
        occupied_end = item.date_end + timedelta(minutes=max(0, item.buffer_after_minutes or 0))
        if occupied_start < end and start < occupied_end:
            return item

    legacy_query = db.query(Visit).filter(
        Visit.specialist_id == specialist_id,
        Visit.deleted_at.is_(None),
        or_(Visit.status.is_(None), Visit.status != "cancelled"),
        ~Visit.items.any(),
        Visit.date_start < end,
        # old rows may have no end - treat them as one hour long
        or_(Visit.date_end > start, and_(Visit.date_end.is_(None), Visit.date_start > start - timedelta(hours=1))),
    )
    if exclude_id:
        legacy_query = legacy_query.filter(Visit.id != exclude_id)
    return legacy_query.first()
