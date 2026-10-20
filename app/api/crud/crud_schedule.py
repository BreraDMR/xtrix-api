from datetime import date, time
from typing import Optional, List

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.db.models.work_schedule import WorkSchedule
from app.db.models.schedule_exception import ScheduleException
from app.db.models.block_slot import BlockSlot
from app.schemas.work_schedule import WorkScheduleCreate, WorkScheduleUpdate
from app.schemas.schedule_exception import ScheduleExceptionCreate, ScheduleExceptionUpdate
from app.schemas.block_slot import BlockSlotCreate, BlockSlotUpdate
from app.api.core.date_validation import validate_time_range
from app.db.models.user import User


def _validate_interval(start: time, end: time) -> None:
    is_valid, message = validate_time_range(start, end)
    if not is_valid:
        raise HTTPException(status_code=422, detail=message)


def _check_specialist(db: Session, specialist_id: int) -> None:
    specialist = db.query(User).filter(
        User.id == specialist_id,
        User.deleted_at.is_(None),
        User.archived_at.is_(None),
    ).first()
    if specialist is None:
        raise HTTPException(status_code=404, detail="Specialist not found")

def create_work_schedule(db: Session, data: WorkScheduleCreate) -> WorkSchedule:
    _validate_interval(data.start_time, data.end_time)
    _check_specialist(db, data.specialist_id)
    obj = WorkSchedule(**data.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj
def list_work_schedules(db: Session, specialist_id: int) -> List[WorkSchedule]:
    return (
        db.query(WorkSchedule)
        .filter(WorkSchedule.specialist_id == specialist_id)
        .order_by(WorkSchedule.weekday, WorkSchedule.start_time)
        .all()
    )
def update_work_schedule(db: Session, obj: WorkSchedule, data: WorkScheduleUpdate) -> WorkSchedule:
    changes = data.model_dump(exclude_unset=True)
    if any(value is None for value in changes.values()):
        raise HTTPException(status_code=422, detail="Schedule times cannot be null")
    start = changes.get("start_time", obj.start_time)
    end = changes.get("end_time", obj.end_time)
    _validate_interval(start, end)
    for k, v in changes.items():
        setattr(obj, k, v)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj
def get_work_schedule(db: Session, ws_id: int) -> Optional[WorkSchedule]:
    return db.query(WorkSchedule).filter(WorkSchedule.id == ws_id).first()
def delete_work_schedule(db: Session, obj: WorkSchedule) -> None:
    db.delete(obj)
    db.commit()


def _check_exception_date_conflict(
    db: Session,
    specialist_id: int,
    exception_date: date,
    exclude_id: Optional[int] = None,
) -> None:
    query = db.query(ScheduleException).filter(
        ScheduleException.specialist_id == specialist_id,
        ScheduleException.exception_date == exception_date,
    )
    if exclude_id is not None:
        query = query.filter(ScheduleException.id != exclude_id)
    if query.first() is not None:
        raise HTTPException(status_code=409, detail="Schedule exception already exists for this date")


def _commit_exception(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Schedule exception already exists for this date")


def create_schedule_exception(db: Session, data: ScheduleExceptionCreate) -> ScheduleException:
    _check_specialist(db, data.specialist_id)
    _check_exception_date_conflict(db, data.specialist_id, data.exception_date)
    obj = ScheduleException(**data.model_dump())
    db.add(obj)
    _commit_exception(db)
    db.refresh(obj)
    return obj


def list_schedule_exceptions(
    db: Session,
    specialist_id: int,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> List[ScheduleException]:
    query = db.query(ScheduleException).filter(ScheduleException.specialist_id == specialist_id)
    if date_from is not None:
        query = query.filter(ScheduleException.exception_date >= date_from)
    if date_to is not None:
        query = query.filter(ScheduleException.exception_date <= date_to)
    return query.order_by(ScheduleException.exception_date).all()


def update_schedule_exception(
    db: Session, obj: ScheduleException, data: ScheduleExceptionUpdate
) -> ScheduleException:
    changes = data.model_dump(exclude_unset=True)
    exception_date = changes.get("exception_date", obj.exception_date)
    if exception_date != obj.exception_date:
        _check_exception_date_conflict(db, obj.specialist_id, exception_date, exclude_id=obj.id)
    is_closed = changes.get("is_closed", obj.is_closed)
    if not is_closed:
        start = changes.get("start_time", obj.start_time)
        end = changes.get("end_time", obj.end_time)
        if start is None or end is None:
            raise HTTPException(status_code=422, detail="Custom working hours need both start and end")
        _validate_interval(start, end)
    for key, value in changes.items():
        setattr(obj, key, value)
    if is_closed:
        obj.start_time = None
        obj.end_time = None
    db.add(obj)
    _commit_exception(db)
    db.refresh(obj)
    return obj


def get_schedule_exception(db: Session, exception_id: int) -> Optional[ScheduleException]:
    return db.query(ScheduleException).filter(ScheduleException.id == exception_id).first()


def delete_schedule_exception(db: Session, obj: ScheduleException) -> None:
    db.delete(obj)
    db.commit()
def create_block_slot(db: Session, data: BlockSlotCreate) -> BlockSlot:
    obj = BlockSlot(**data.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj
def list_block_slots(db: Session, specialist_id: int) -> List[BlockSlot]:
    return (
        db.query(BlockSlot)
        .filter(BlockSlot.specialist_id == specialist_id)
        .order_by(BlockSlot.start)
        .all()
    )
def update_block_slot(db: Session, obj: BlockSlot, data: BlockSlotUpdate) -> BlockSlot:
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj
def get_block_slot(db: Session, bs_id: int) -> Optional[BlockSlot]:
    return db.query(BlockSlot).filter(BlockSlot.id == bs_id).first()
def delete_block_slot(db: Session, obj: BlockSlot) -> None:
    db.delete(obj)
    db.commit()
