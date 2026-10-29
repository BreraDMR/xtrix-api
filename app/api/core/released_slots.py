"""Immutable old slot metadata for best-effort work after the owning commit."""
from dataclasses import dataclass
from datetime import datetime,timedelta

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app.api.core import visitor_action_target as targets
from app.api.core.logger import logger
from app.db.models.visit_item import VisitItem


@dataclass(frozen=True)
class ReleasedSlot:
    service_id: int | None
    specialist_id: int
    start: datetime
    end: datetime | None = None


def visit_slots(visit):
    items = sorted(visit.items, key=lambda item: item.position)
    return tuple(ReleasedSlot(item.service_id,item.specialist_id,item.date_start,item.date_end) for item in items) or (
        ReleasedSlot(visit.service_id,visit.specialist_id,visit.date_start,visit.date_end),)


def chain_slots(lines,start):
    slots=[]
    for line in lines:
        end=start+timedelta(minutes=line.duration_minutes)
        slots.append(ReleasedSlot(line.service_id,line.specialist_id,start,end));start=end
    return tuple(slots)


def changed_slots(before,after,old_status,new_status):
    if old_status=='cancelled':
        return ()
    if new_status=='cancelled':
        return tuple(dict.fromkeys(before))
    occupied={(slot.specialist_id,slot.start,slot.end) for slot in after}
    return tuple(dict.fromkeys(slot for slot in before if (slot.specialist_id,slot.start,slot.end) not in occupied))


def cancellation_slots(db,target,service_id):
    """Visit is already owned. A busy child must fail before status-trigger writes."""
    with db.no_autoflush:
        if any(isinstance(row,VisitItem) for row in db.new.union(db.dirty).union(db.deleted)):
            raise targets.DirtyActionState('Save item changes before cancelling a booking')
        try:
            rows=db.execute(select(VisitItem.service_id,VisitItem.specialist_id,VisitItem.date_start,VisitItem.date_end)
                .where(VisitItem.visit_id==target.visit_id).order_by(VisitItem.position,VisitItem.id)
                .with_for_update(read=True,nowait=True)).all()
        except DBAPIError as error:
            targets._busy_or_raise(error)
        return tuple(ReleasedSlot(*row) for row in rows) or (
            ReleasedSlot(service_id,target.specialist_id,target.date_start),)


def match_quietly(db,business,slots):
    if business is None:
        return
    from app.api.core import waitlist_service
    for slot in dict.fromkeys(slots):
        try:
            waitlist_service.release_slot_quietly(db,business,slot.service_id,slot.specialist_id,slot.start)
        except Exception as exc:
            db.rollback()
            logger.warning('Post-commit chain slot matching failed: %s',type(exc).__name__)
