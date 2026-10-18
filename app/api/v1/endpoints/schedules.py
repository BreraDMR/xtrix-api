from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.api import deps
from app.api.core.i18n import _
from app.api.crud import crud_schedule
from app.db.models.user import User
from app.schemas.work_schedule import WorkScheduleCreate, WorkScheduleOut, WorkScheduleUpdate
from app.schemas.block_slot import BlockSlotCreate, BlockSlotOut, BlockSlotUpdate
router = APIRouter()

def _can_manage_schedule(db: Session, user: User, specialist_id: int) -> bool:
    if user.role == "admin" or user.id == specialist_id:
        return True
    if user.role != "business_owner":
        return False
    from app.db.models.employee import Employee
    return db.query(Employee).join(User, Employee.employee_user_id == User.id).filter(
        Employee.business_owner_id == user.id,
        Employee.employee_user_id == specialist_id,
        Employee.is_active.is_(True),
        User.deleted_at.is_(None),
    ).first() is not None

@router.post("/work", response_model=WorkScheduleOut)
def create_work_schedule(
    payload: WorkScheduleCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist")),
):
    if not _can_manage_schedule(db, current_user, payload.specialist_id):
        raise HTTPException(status_code=403, detail=_("not_allowed"))
    return crud_schedule.create_work_schedule(db, payload)
@router.get("/work", response_model=list[WorkScheduleOut])
def list_work_schedules(
    specialist_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist")),
):
    if not _can_manage_schedule(db, current_user, specialist_id):
        raise HTTPException(status_code=403, detail=_("not_allowed"))
    return crud_schedule.list_work_schedules(db, specialist_id)
@router.put("/work/{ws_id}", response_model=WorkScheduleOut)
def update_work_schedule(
    ws_id: int,
    payload: WorkScheduleUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist")),
):
    ws = crud_schedule.get_work_schedule(db, ws_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Not found")
    if not _can_manage_schedule(db, current_user, ws.specialist_id):
        raise HTTPException(status_code=403, detail=_("not_allowed"))
    return crud_schedule.update_work_schedule(db, ws, payload)
@router.post("/blocks", response_model=BlockSlotOut)
def create_block(
    payload: BlockSlotCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist")),
):
    if not _can_manage_schedule(db, current_user, payload.specialist_id):
        raise HTTPException(status_code=403, detail=_("not_allowed"))
    return crud_schedule.create_block_slot(db, payload)
@router.get("/blocks", response_model=list[BlockSlotOut])
def list_blocks(
    specialist_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist")),
):
    if not _can_manage_schedule(db, current_user, specialist_id):
        raise HTTPException(status_code=403, detail=_("not_allowed"))
    return crud_schedule.list_block_slots(db, specialist_id)
@router.put("/blocks/{block_id}", response_model=BlockSlotOut)
def update_block(
    block_id: int,
    payload: BlockSlotUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist")),
):
    blk = crud_schedule.get_block_slot(db, block_id)
    if not blk:
        raise HTTPException(status_code=404, detail="Not found")
    if not _can_manage_schedule(db, current_user, blk.specialist_id):
        raise HTTPException(status_code=403, detail=_("not_allowed"))
    return crud_schedule.update_block_slot(db, blk, payload)
