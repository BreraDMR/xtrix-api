from datetime import datetime, timedelta, time
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.api import deps
from app.api.core.i18n import _, localize_response
from app.api.core.notify import send_notifications
from app.api.core.pagination import PaginationParams, PaginatedResponse
from app.api.core.soft_delete import hard_delete, restore_soft_deleted, soft_delete
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
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    from app.api.core.permissions import can_create_visits, check_client_access
    if not can_create_visits(current_user, db):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    if visit.specialist_id is None or visit.specialist_id == 0:
        visit.specialist_id = current_user.id
    business_id = current_user.business_id if hasattr(current_user, 'business_id') else None
    specialist_id_for_links = current_user.id
    if current_user.role == "employee":
        from app.api.crud import crud_employee
        employee = crud_employee.get_employee_by_user_id(db, current_user.id)
        if employee:
            specialist_id_for_links = employee.business_owner_id
    if business_id:
        from app.db.models.client_business_relation import ClientBusinessRelation
        from app.db.models.client_specialist_relation import ClientSpecialistRelation
        existing_business_relation = db.query(ClientBusinessRelation).filter(
            ClientBusinessRelation.client_id == visit.client_id,
            ClientBusinessRelation.business_id == business_id
        ).first()
        if not existing_business_relation:
            business_relation = ClientBusinessRelation(
                client_id=visit.client_id,
                business_id=business_id
            )
            db.add(business_relation)
            db.flush()
        if current_user.role != "admin" and specialist_id_for_links:
            existing_spec_relation = db.query(ClientSpecialistRelation).filter(
                ClientSpecialistRelation.client_id == visit.client_id,
                ClientSpecialistRelation.specialist_id == specialist_id_for_links,
                ClientSpecialistRelation.business_id == business_id
            ).first()
            if not existing_spec_relation:
                specialist_relation = ClientSpecialistRelation(
                    client_id=visit.client_id,
                    specialist_id=specialist_id_for_links,
                    business_id=business_id
                )
                db.add(specialist_relation)
                db.flush()
        db.commit()
    if not check_client_access(db, current_user, visit.client_id):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    used_products = visit.used_products if hasattr(visit, 'used_products') else None
    result = crud_visit.create_visit(db, visit, business_id=business_id)
    if business_id and used_products:
        from app.api.crud import crud_product_usage
        from app.schemas.product_usage import ProductUsageCreate
        for used_product in used_products:
            try:
                usage = ProductUsageCreate(
                    product_id=used_product.product_id,
                    quantity=used_product.quantity,
                    visit_id=result.id,
                    business_id=business_id,
                    client_id=visit.client_id,
                    used_by_id=current_user.id,
                    notes=f"Автоматическое списание при визите #{result.id}",
                )
                crud_product_usage.create_product_usage(db, usage, auto_reduce_stock=True)
                logger.info(f"Auto-usage: product {used_product.product_id}, quantity {used_product.quantity}, visit {result.id}")
            except Exception as e:
                logger.error(f"Failed to auto-usage product {used_product.product_id} for visit {result.id}: {e}")
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
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    from app.api.core.permissions import get_filtered_specialist_id, get_accessible_client_ids
    if current_user.role == "admin":
        specialist_id = None
    elif current_user.role == "business_owner":
        specialist_id = None  
    else:
        specialist_id = get_filtered_specialist_id(current_user)
    if client_id:
        from app.api.core.permissions import check_client_access
        if not check_client_access(db, current_user, client_id):
            raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    if current_user.role != "admin":
        include_deleted = False
        include_archived = False
    business_id = current_user.business_id if hasattr(current_user, 'business_id') else None
    pagination = PaginationParams(page=page, per_page=per_page)
    visits, total = crud_visit.list_visits(
        db,
        client_id=client_id,
        specialist_id=specialist_id,
        business_id=business_id,  
        date_from=date_from,
        date_to=date_to,
        pagination=pagination,
        include_deleted=include_deleted,
        include_archived=include_archived,
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
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    business_id = current_user.business_id if hasattr(current_user, 'business_id') else None
    if current_user.role != "admin":
        include_deleted = False
    visit = crud_visit.get_visit(db, visit_id, include_deleted=include_deleted, business_id=business_id)
    if not visit:
        raise HTTPException(status_code=404, detail=_("visit_not_found", locale))
    from app.api.core.permissions import check_visit_access
    if not check_visit_access(db, current_user, visit):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    return localize_response(visit.dict() if hasattr(visit, 'dict') else visit, locale)
@router.put("/{visit_id}", response_model=VisitOut)
def update_visit(
    visit_id: int,
    payload: VisitUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    business_id = current_user.business_id if hasattr(current_user, 'business_id') else None
    visit = crud_visit.get_visit(db, visit_id, business_id=business_id)
    if not visit:
        raise HTTPException(status_code=404, detail=_("visit_not_found", locale))
    from app.api.core.permissions import check_visit_access
    if not check_visit_access(db, current_user, visit):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    final_status = payload.status if payload.status is not None else visit.status
    final_price = payload.price if payload.price is not None else visit.price
    final_payment_method = payload.payment_method if payload.payment_method is not None else visit.payment_method
    if final_status == 'completed' and final_price and final_price > 0:
        if not final_payment_method or final_payment_method not in ['card', 'cash', 'other']:
            raise HTTPException(
                status_code=400,
                detail=_("payment_method_required_for_completed_visits", locale) or "Payment method is required for completed visits with price"
            )
    old_status = visit.status
    result = crud_visit.update_visit(db, visit, payload)
    if old_status != 'completed' and result.status == 'completed':
        try:
            from app.api.core.loyalty_logic import award_bonus_for_visit
            award_bonus_for_visit(db, result)
        except Exception as e:
            from app.api.core.logger import logger
            logger.error(f"Error awarding loyalty bonus for visit {result.id}: {e}")
    from app.api.core.i18n import localize_response
    return localize_response(result.dict() if hasattr(result, 'dict') else result, locale)
@router.delete("/{visit_id}")
def delete_visit(
    visit_id: int,
    hard_delete_flag: bool = Query(False, description="Permanently delete (admin only)", alias="hard_delete"),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    visit = crud_visit.get_visit(db, visit_id)
    if not visit:
        raise HTTPException(status_code=404, detail=_("visit_not_found", locale))
    from app.api.core.permissions import check_visit_access
    if not check_visit_access(db, current_user, visit):
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
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    visit = crud_visit.get_visit(db, visit_id, include_deleted=True)
    if not visit:
        raise HTTPException(status_code=404, detail=_("visit_not_found", locale))
    from app.api.core.permissions import check_visit_access
    if not check_visit_access(db, current_user, visit):
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
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    if current_user.role != "admin" and specialist_id != current_user.id:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
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
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
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
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    due = visits_for_reminders(db=db, current_user=current_user)
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
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
):
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
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
):
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
