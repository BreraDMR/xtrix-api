from typing import Optional, List
from sqlalchemy.orm import Session

from app.db.models.work_schedule import WorkSchedule
from app.db.models.block_slot import BlockSlot
from app.schemas.work_schedule import WorkScheduleCreate, WorkScheduleUpdate
from app.schemas.block_slot import BlockSlotCreate, BlockSlotUpdate


# WorkSchedule
def create_work_schedule(db: Session, data: WorkScheduleCreate) -> WorkSchedule:
    obj = WorkSchedule(**data.dict())
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
    for k, v in data.dict(exclude_unset=True).items():
        setattr(obj, k, v)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def get_work_schedule(db: Session, ws_id: int) -> Optional[WorkSchedule]:
    return db.query(WorkSchedule).filter(WorkSchedule.id == ws_id).first()


# BlockSlot
def create_block_slot(db: Session, data: BlockSlotCreate) -> BlockSlot:
    obj = BlockSlot(**data.dict())
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
    for k, v in data.dict(exclude_unset=True).items():
        setattr(obj, k, v)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def get_block_slot(db: Session, bs_id: int) -> Optional[BlockSlot]:
    return db.query(BlockSlot).filter(BlockSlot.id == bs_id).first()











