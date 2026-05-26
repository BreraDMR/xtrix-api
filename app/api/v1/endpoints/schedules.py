from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.api import deps
from app.api.core.i18n import _
from app.api.crud import crud_schedule
from app.db.models.user import User
from app.db.models.work_schedule import WorkSchedule
from app.db.models.block_slot import BlockSlot
from app.schemas.work_schedule import WorkScheduleCreate, WorkScheduleOut, WorkScheduleUpdate
from app.schemas.block_slot import BlockSlotCreate, BlockSlotOut, BlockSlotUpdate
router = APIRouter()
@router.post("/work", response_model=WorkScheduleOut)
def create_work_schedule(
    payload: WorkScheduleCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
):
    if current_user.role != "admin" and payload.specialist_id != current_user.id:
        raise HTTPException(status_code=403, detail=_("not_allowed"))
    return crud_schedule.create_work_schedule(db, payload)
@router.get("/work", response_model=list[WorkScheduleOut])
def list_work_schedules(
    specialist_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
):
    if current_user.role != "admin" and specialist_id != current_user.id:
        raise HTTPException(status_code=403, detail=_("not_allowed"))
    return crud_schedule.list_work_schedules(db, specialist_id)
@router.put("/work/{ws_id}", response_model=WorkScheduleOut)
def update_work_schedule(
    ws_id: int,
    payload: WorkScheduleUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
):
    ws = crud_schedule.get_work_schedule(db, ws_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Not found")
    if current_user.role != "admin" and ws.specialist_id != current_user.id:
        raise HTTPException(status_code=403, detail=_("not_allowed"))
    return crud_schedule.update_work_schedule(db, ws, payload)
@router.post("/blocks", response_model=BlockSlotOut)
def create_block(
    payload: BlockSlotCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
):
    if current_user.role != "admin" and payload.specialist_id != current_user.id:
        raise HTTPException(status_code=403, detail=_("not_allowed"))
    return crud_schedule.create_block_slot(db, payload)
@router.get("/blocks", response_model=list[BlockSlotOut])
def list_blocks(
    specialist_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
):
    if current_user.role != "admin" and specialist_id != current_user.id:
        raise HTTPException(status_code=403, detail=_("not_allowed"))
    return crud_schedule.list_block_slots(db, specialist_id)
@router.put("/blocks/{block_id}", response_model=BlockSlotOut)
def update_block(
    block_id: int,
    payload: BlockSlotUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
):
    blk = crud_schedule.get_block_slot(db, block_id)
    if not blk:
        raise HTTPException(status_code=404, detail="Not found")
    if current_user.role != "admin" and blk.specialist_id != current_user.id:
        raise HTTPException(status_code=403, detail=_("not_allowed"))
    return crud_schedule.update_block_slot(db, blk, payload)
