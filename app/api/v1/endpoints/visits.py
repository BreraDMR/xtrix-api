from datetime import datetime, timedelta, time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core.i18n import _, localize_response
from app.api.core.notify import send_notifications
from app.api.core.pagination import PaginationParams, PaginatedResponse
from app.db.models.client import Client
from app.api.core.slots import find_next_available_slot
from app.api.crud import crud_visit, crud_schedule
from app.db.models.service import Service
from app.db.models.user import User
from app.schemas.visit import VisitCreate, VisitOut, VisitUpdate

router = APIRouter()


@router.post("/", response_model=VisitOut)
def create_visit(
    visit: VisitCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
    locale: str = Depends(deps.get_locale),
):
    if current_user.role != "admin" and visit.specialist_id != current_user.id:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    result = crud_visit.create_visit(db, visit)
    from app.api.core.i18n import localize_response
    return localize_response(result.dict() if hasattr(result, 'dict') else result, locale)


@router.get("/", response_model=PaginatedResponse[VisitOut])
def visits_list(
    client_id: Optional[int] = None,
    specialist_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    include_deleted: bool = Query(False, description="Include soft-deleted visits"),
    include_archived: bool = Query(False, description="Include archived visits"),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
    locale: str = Depends(deps.get_locale),
):
    if current_user.role != "admin":
        specialist_id = current_user.id
        # Non-admin users cannot see deleted/archived
        include_deleted = False
        include_archived = False
    
    pagination = PaginationParams(page=page, per_page=per_page)
    visits, total = crud_visit.list_visits(
        db,
        client_id=client_id,
        specialist_id=specialist_id,
        date_from=date_from,
        date_to=date_to,
        pagination=pagination,
    )
    
    localized_items = [
        localize_response(v.dict() if hasattr(v, 'dict') else v, locale) for v in visits
    ]
    
    return PaginatedResponse.create(
        items=localized_items,
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/{visit_id}", response_model=VisitOut)
def get_visit(
    visit_id: int,
    include_deleted: bool = Query(False, description="Include soft-deleted visit"),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
    locale: str = Depends(deps.get_locale),
):
    if current_user.role != "admin":
        include_deleted = False
    
    visit = crud_visit.get_visit(db, visit_id, include_deleted=include_deleted)
    if not visit:
        raise HTTPException(status_code=404, detail=_("visit_not_found", locale))
    if current_user.role != "admin" and visit.specialist_id != current_user.id:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    return localize_response(visit.dict() if hasattr(visit, 'dict') else visit, locale)


@router.put("/{visit_id}", response_model=VisitOut)
def update_visit(
    visit_id: int,
    payload: VisitUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
    locale: str = Depends(deps.get_locale),
):
    visit = crud_visit.get_visit(db, visit_id)
    if not visit:
        raise HTTPException(status_code=404, detail=_("visit_not_found", locale))
    if current_user.role != "admin" and visit.specialist_id != current_user.id:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    result = crud_visit.update_visit(db, visit, payload)
    from app.api.core.i18n import localize_response
    return localize_response(result.dict() if hasattr(result, 'dict') else result, locale)


@router.delete("/{visit_id}")
def delete_visit(
    visit_id: int,
    hard_delete_flag: bool = Query(False, description="Permanently delete (admin only)", alias="hard_delete"),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
    locale: str = Depends(deps.get_locale),
):
    """Delete visit (soft delete by default, hard delete if hard_delete=True and user is admin)."""
    visit = crud_visit.get_visit(db, visit_id)
    if not visit:
        raise HTTPException(status_code=404, detail=_("visit_not_found", locale))
    if current_user.role != "admin" and visit.specialist_id != current_user.id:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    
    if hard_delete_flag and current_user.role == "admin":
        hard_delete(db, visit)
        return {"message": "Visit permanently deleted"}
    else:
        soft_delete(db, visit)
        return {"message": "Visit deleted (soft delete)"}


@router.post("/{visit_id}/restore")
def restore_visit(
    visit_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
    locale: str = Depends(deps.get_locale),
):
    """Restore a soft-deleted visit."""
    visit = crud_visit.get_visit(db, visit_id, include_deleted=True)
    if not visit:
        raise HTTPException(status_code=404, detail=_("visit_not_found", locale))
    if current_user.role != "admin" and visit.specialist_id != current_user.id:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    
    restore_soft_deleted(db, visit)
    return {"message": "Visit restored successfully"}


@router.get("/next_slot")
def next_available_slot(
    specialist_id: int,
    service_id: Optional[int] = None,
    duration_minutes: Optional[int] = None,
    work_start: str = "09:00",
    work_end: str = "18:00",
    break_start: Optional[str] = None,
    break_end: Optional[str] = None,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
    locale: str = Depends(deps.get_locale),
):
    if current_user.role != "admin" and specialist_id != current_user.id:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    # Determine duration
    if not duration_minutes and service_id:
        service = db.query(Service).filter(Service.id == service_id).first()
        duration_minutes = service.duration_minutes if service and service.duration_minutes else 60
    duration_minutes = duration_minutes or 60

    visits, _ = crud_visit.list_visits(
        db,
        specialist_id=specialist_id,
        include_deleted=False,
        include_archived=False,
    )
    existing = []
    for v in visits:
        if v.date_end:
            end = v.date_end
        else:
            end = v.date_start + timedelta(minutes=duration_minutes)
        existing.append((v.date_start, end))
    schedules = crud_schedule.list_work_schedules(db, specialist_id)
    blocks = crud_schedule.list_block_slots(db, specialist_id)
    schedule_tuples = [(s.weekday, s.start_time, s.end_time) for s in schedules] if schedules else None
    block_tuples = [(b.start, b.end) for b in blocks] if blocks else None
    slot = find_next_available_slot(
        existing,
        duration_minutes,
        workday_start=_parse_time(work_start),
        workday_end=_parse_time(work_end),
        breaks=_build_breaks(break_start, break_end),
        schedule=schedule_tuples,
        blocks=block_tuples,
    )
    if not slot:
        raise HTTPException(status_code=404, detail=_("no_slot", locale))
    return {"next_slot": slot}


@router.get("/reminders")
def visits_for_reminders(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
):
    now = datetime.utcnow()
    horizon = now + timedelta(hours=24)
    visits, _ = crud_visit.list_visits(
        db,
        specialist_id=None if current_user.role == "admin" else current_user.id,
        include_deleted=False,
        include_archived=False,
    )
    due = [
        v
        for v in visits
        if v.date_start >= now and v.date_start <= horizon and v.status == "planned"
    ]
    return due


@router.post("/reminders/send")
def send_reminders(
    channel: str = "log",
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
    locale: str = Depends(deps.get_locale),
):
    due = visits_for_reminders(db=db, current_user=current_user)
    # собрать контакты клиентов
    client_ids = {v.client_id for v in due}
    contacts = {}
    if client_ids:
        clients = db.query(Client).filter(Client.id.in_(client_ids)).all()
        contacts = {
            c.id: {"email": c.contact_email, "phone": c.contact_phone} for c in clients
        }
    sent = send_notifications(due, channel=channel, contacts=contacts)
    return {"sent": sent, "channel": channel, "message": _("reminders_sent", locale)}


def _parse_time(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def _build_breaks(start: Optional[str], end: Optional[str]):
    if start and end:
        return [( _parse_time(start), _parse_time(end))]
    return []


@router.get("/export/csv")
def export_visits_csv(
    client_id: Optional[int] = None,
    specialist_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
):
    """Export visits to CSV."""
    from app.api.core.export import export_to_csv, prepare_visit_export_data
    
    if current_user.role != "admin":
        specialist_id = current_user.id
    
    visits, _ = crud_visit.list_visits(
        db,
        client_id=client_id,
        specialist_id=specialist_id,
        date_from=date_from,
        date_to=date_to,
        include_deleted=False,
        include_archived=False,
    )
    export_data = prepare_visit_export_data(visits)
    return export_to_csv(export_data, filename="visits_export.csv")


@router.get("/export/excel")
def export_visits_excel(
    client_id: Optional[int] = None,
    specialist_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
):
    """Export visits to Excel."""
    from app.api.core.export import export_to_excel, prepare_visit_export_data
    
    if current_user.role != "admin":
        specialist_id = current_user.id
    
    visits, _ = crud_visit.list_visits(
        db,
        client_id=client_id,
        specialist_id=specialist_id,
        date_from=date_from,
        date_to=date_to,
        include_deleted=False,
        include_archived=False,
    )
    export_data = prepare_visit_export_data(visits)
    return export_to_excel(export_data, filename="visits_export.xlsx", sheet_name="Visits")
