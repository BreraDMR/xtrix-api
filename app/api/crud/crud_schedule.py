from datetime import time
from typing import Optional, List

from fastapi import HTTPException
from sqlalchemy.orm import Session
from app.db.models.work_schedule import WorkSchedule
from app.db.models.block_slot import BlockSlot
from app.schemas.work_schedule import WorkScheduleCreate, WorkScheduleUpdate
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
