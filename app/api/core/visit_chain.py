"""Ordered services inside one visit.

The client sends only service ids (and optionally a master per item). The server snapshots
name, price, duration and buffers, lays the items out back to back from the visit start,
and computes the one total stored in ``Visit.price``. Analytics, loyalty and payroll keep
reading that total, so adding item rows never doubles revenue.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Sequence

from sqlalchemy.orm import Session

from app.db.models.service import Service
from app.db.models.visit import Visit
from app.db.models.visit_item import VisitItem
from app.schemas.visit import VisitItemIn

MONEY = Decimal("0.01")


class ChainError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ChainLine:
    service_id: Optional[int]
    specialist_id: Optional[int]
    service_name: Optional[str]
    price: Optional[Decimal]
    duration_minutes: int
    buffer_before_minutes: int = 0
    buffer_after_minutes: int = 0


def money(value) -> Optional[Decimal]:
    if value is None:
        return None
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def _duration(service: Optional[Service], start: datetime, end: Optional[datetime]) -> int:
    if end is not None and end > start:
        return max(1, int(round((end - start).total_seconds() / 60)))
    if service is not None and service.duration_minutes and service.duration_minutes > 0:
        return int(service.duration_minutes)
    return 60


def service_line(service: Service, specialist_id: Optional[int]) -> ChainLine:
    if not service.duration_minutes or service.duration_minutes <= 0:
        raise ChainError("service_duration_required", "Every service needs a duration")
    return ChainLine(
        service_id=service.id,
        specialist_id=specialist_id,
        service_name=service.name,
        price=money(service.price),
        duration_minutes=int(service.duration_minutes),
        buffer_before_minutes=max(0, service.buffer_before_minutes or 0),
        buffer_after_minutes=max(0, service.buffer_after_minutes or 0),
    )


def legacy_line(
    service: Optional[Service],
    specialist_id: Optional[int],
    service_name: Optional[str],
    price,
    start: datetime,
    end: Optional[datetime],
) -> ChainLine:
    """A request without items: one service, or a manual visit with no service at all."""
    return ChainLine(
        service_id=service.id if service is not None else None,
        specialist_id=specialist_id,
        service_name=(service.name if service is not None else service_name),
        price=money(price),
        duration_minutes=_duration(service, start, end),
        buffer_before_minutes=max(0, service.buffer_before_minutes or 0) if service else 0,
        buffer_after_minutes=max(0, service.buffer_after_minutes or 0) if service else 0,
    )


def lines_from_items(
    db: Session,
    items: Sequence[VisitItemIn],
    default_specialist_id: int,
) -> list[ChainLine]:
    if not items:
        raise ChainError("items_required", "A visit needs at least one service")
    ids = {item.service_id for item in items}
    services = {
        service.id: service
        for service in db.query(Service).filter(Service.id.in_(ids)).all()
    }
    lines = []
    for item in items:
        service = services.get(item.service_id)
        if service is None:
            raise ChainError("service_not_found", "The selected service was not found")
        lines.append(
            service_line(service, item.specialist_id or default_specialist_id)
        )
    return lines


def total_price(lines: Sequence[ChainLine]) -> Optional[Decimal]:
    prices = [line.price for line in lines if line.price is not None]
    if not prices:
        return None
    return sum(prices, Decimal("0")).quantize(MONEY, rounding=ROUND_HALF_UP)


def total_minutes(lines: Sequence[ChainLine]) -> int:
    return sum(line.duration_minutes for line in lines)


def build_items(start: datetime, lines: Sequence[ChainLine]) -> list[VisitItem]:
    rows: list[VisitItem] = []
    cursor = start
    for position, line in enumerate(lines):
        end = cursor + timedelta(minutes=max(1, line.duration_minutes))
        rows.append(
            VisitItem(
                position=position,
                service_id=line.service_id,
                specialist_id=line.specialist_id,
                service_name=line.service_name,
                price=line.price,
                duration_minutes=max(1, line.duration_minutes),
                buffer_before_minutes=line.buffer_before_minutes,
                buffer_after_minutes=line.buffer_after_minutes,
                date_start=cursor,
                date_end=end,
            )
        )
        cursor = end
    return rows


def item_intervals(
    start: datetime, lines: Sequence[ChainLine], with_buffers: bool = True
) -> list[tuple[Optional[int], datetime, datetime]]:
    """(specialist_id, occupied start, occupied end) for conflict checks."""
    intervals = []
    for row in build_items(start, lines):
        before = timedelta(minutes=row.buffer_before_minutes) if with_buffers else timedelta()
        after = timedelta(minutes=row.buffer_after_minutes) if with_buffers else timedelta()
        intervals.append(
            (row.specialist_id, row.date_start - before, row.date_end + after)
        )
    return intervals


def apply_chain(visit: Visit, lines: Sequence[ChainLine], start: datetime) -> None:
    """Replace the visit's items and recompute every legacy field from them.

    Existing rows are updated in place instead of delete+insert: a unique
    (visit_id, position) key would otherwise collide when the ORM inserts before deleting.
    """
    rows = build_items(start, lines)
    names = [line.service_name for line in lines if line.service_name]
    visit.date_start = start
    visit.date_end = rows[-1].date_end
    total = total_price(lines)
    visit.price = float(total) if total is not None else None
    visit.service_id = lines[0].service_id
    visit.service_name = " + ".join(names) if names else None
    if lines[0].specialist_id is not None:
        visit.specialist_id = lines[0].specialist_id

    existing = list(visit.items)
    for position, row in enumerate(rows):
        if position < len(existing):
            target = existing[position]
            target.position = position
            target.service_id = row.service_id
            target.specialist_id = row.specialist_id
            target.service_name = row.service_name
            target.price = row.price
            target.duration_minutes = row.duration_minutes
            target.buffer_before_minutes = row.buffer_before_minutes
            target.buffer_after_minutes = row.buffer_after_minutes
            target.date_start = row.date_start
            target.date_end = row.date_end
        else:
            visit.items.append(row)
    for extra in existing[len(rows):]:
        visit.items.remove(extra)
