from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.api import deps
from app.api.core.i18n import _, localize_response
from app.api.core.pagination import PaginationParams, PaginatedResponse
from app.api.core.soft_delete import hard_delete, restore_soft_deleted, soft_delete
from app.api.crud import crud_service
from app.db.models.service import Service
from app.db.models.user import User
from app.schemas.service import ServiceCreate, ServiceOut, ServiceUpdate
router = APIRouter()
def _check_specialists(db: Session, user: User, specialist_ids, locale: str) -> None:
    # owners used to be able to put any user on the platform down as the specialist
    from app.api.core.permissions import check_specialist_in_scope
    for sid in specialist_ids:
        if sid and not check_specialist_in_scope(db, user, sid):
            raise HTTPException(status_code=403, detail=_("not_allowed", locale))
@router.post("/", response_model=ServiceOut)
def create_service(
    service: ServiceCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    from app.api.core.permissions import can_manage_services
    if not can_manage_services(current_user, db):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    if service.specialist_id is None or service.specialist_id == 0:
        service.specialist_id = current_user.id
    _check_specialists(db, current_user, [service.specialist_id] + (service.specialist_ids or []), locale)
    business_id = current_user.business_id if hasattr(current_user, 'business_id') else None
    result = crud_service.create_service(db, service, business_id=business_id)
    result = crud_service.get_service(db, result.id)
    service_dict = result.dict() if hasattr(result, 'dict') else result.__dict__.copy()
    specialist_ids = [ss.specialist_id for ss in result.service_specialists] if hasattr(result, 'service_specialists') and result.service_specialists else [result.specialist_id]
    service_dict['specialist_ids'] = specialist_ids
    specialists = []
    if hasattr(result, 'service_specialists') and result.service_specialists:
        for ss in result.service_specialists:
            if ss.specialist:
                specialists.append({
                    'id': ss.specialist.id,
                    'full_name': ss.specialist.full_name,
                    'email': ss.specialist.email,
                    'phone': ss.specialist.phone,
                })
    elif hasattr(result, 'specialist') and result.specialist:
        specialists.append({
            'id': result.specialist.id,
            'full_name': result.specialist.full_name,
            'email': result.specialist.email,
            'phone': result.specialist.phone,
        })
    service_dict['specialists'] = specialists
    return localize_response(service_dict, locale)
@router.get("/", response_model=PaginatedResponse[ServiceOut])
def list_services(
    specialist_id: Optional[int] = None,
    active_only: bool = False,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    business_id = current_user.business_id if hasattr(current_user, 'business_id') else None
    from app.api.core.permissions import get_filtered_specialist_id, can_manage_services
    if not can_manage_services(current_user, db):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    employee_specialist_ids = None  
    if current_user.role == "admin":
        if business_id:
            from app.db.models.employee import Employee
            from app.api.core.permissions import get_business_owner_ids_for_filter
            owner_ids = get_business_owner_ids_for_filter(current_user, db)
            if not owner_ids:
                owner_ids = [current_user.id]
            employees = db.query(Employee.employee_user_id).filter(
                Employee.business_owner_id.in_(owner_ids),
                Employee.is_active == True
            ).all()
            employee_specialist_ids = [emp[0] for emp in employees] if employees else []
        if specialist_id:
            pass
        else:
            specialist_id = None
    elif current_user.role == "business_owner":
        from app.db.models.employee import Employee
        employees = db.query(Employee.employee_user_id).filter(
            Employee.business_owner_id == current_user.id,
            Employee.is_active == True
        ).all()
        employee_specialist_ids = [emp[0] for emp in employees] if employees else []
        specialist_id = None  
    else:
        # specialists and employees only ever list their own services, whatever specialist_id says
        specialist_id = get_filtered_specialist_id(current_user)
    pagination = PaginationParams(page=page, per_page=per_page)
    is_active_filter = True if active_only else None
    services, total = crud_service.list_services(
        db, 
        specialist_id=specialist_id, 
        business_id=business_id,  
        employee_specialist_ids=employee_specialist_ids,  
        is_active=is_active_filter,
        pagination=pagination
    )
    def service_to_dict(service: Service):
        result = service.dict() if hasattr(service, 'dict') else service.__dict__.copy()
        specialist_ids = []
        specialists = []
        if hasattr(service, 'service_specialists') and service.service_specialists:
            for ss in service.service_specialists:
                if ss.specialist:
                    specialist_ids.append(ss.specialist_id)
                    specialists.append({
                        'id': ss.specialist.id,
                        'full_name': ss.specialist.full_name,
                        'email': ss.specialist.email,
                        'phone': ss.specialist.phone,
                    })
        result['specialist_ids'] = specialist_ids
        result['specialists'] = specialists
        return result
    localized_items = [
        localize_response(service_to_dict(s), locale) for s in services
    ]
    return PaginatedResponse.create(
        items=localized_items,
        total=total,
        page=page,
        per_page=per_page,
    )
@router.get("/{service_id}", response_model=ServiceOut)
def get_service(
    service_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    service = crud_service.get_service(db, service_id)
    if not service:
        raise HTTPException(status_code=404, detail=_("service_not_found", locale))
    from app.api.core.permissions import check_service_access
    if not check_service_access(db, current_user, service):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    service_dict = service.dict() if hasattr(service, 'dict') else service.__dict__.copy()
    specialist_ids = []
    specialists = []
    if hasattr(service, 'service_specialists') and service.service_specialists:
        for ss in service.service_specialists:
            if ss.specialist:
                specialist_ids.append(ss.specialist_id)
                specialists.append({
                    'id': ss.specialist.id,
                    'full_name': ss.specialist.full_name,
                    'email': ss.specialist.email,
                    'phone': ss.specialist.phone,
                })
    service_dict['specialist_ids'] = specialist_ids
    service_dict['specialists'] = specialists
    return localize_response(service_dict, locale)
@router.put("/{service_id}", response_model=ServiceOut)
def update_service(
    service_id: int,
    payload: ServiceUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    service = crud_service.get_service(db, service_id)
    if not service:
        raise HTTPException(status_code=404, detail=_("service_not_found", locale))
    from app.api.core.permissions import check_service_access
    if not check_service_access(db, current_user, service):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    _check_specialists(db, current_user, [payload.specialist_id] + (payload.specialist_ids or []), locale)
    result = crud_service.update_service(db, service, payload)
    result = crud_service.get_service(db, result.id)
    service_dict = result.dict() if hasattr(result, 'dict') else result.__dict__.copy()
    specialist_ids = [ss.specialist_id for ss in result.service_specialists] if hasattr(result, 'service_specialists') and result.service_specialists else [result.specialist_id]
    service_dict['specialist_ids'] = specialist_ids
    specialists = []
    if hasattr(result, 'service_specialists') and result.service_specialists:
        for ss in result.service_specialists:
            if ss.specialist:
                specialists.append({
                    'id': ss.specialist.id,
                    'full_name': ss.specialist.full_name,
                    'email': ss.specialist.email,
                    'phone': ss.specialist.phone,
                })
    elif hasattr(result, 'specialist') and result.specialist:
        specialists.append({
            'id': result.specialist.id,
            'full_name': result.specialist.full_name,
            'email': result.specialist.email,
            'phone': result.specialist.phone,
        })
    service_dict['specialists'] = specialists
    return localize_response(service_dict, locale)
@router.delete("/{service_id}")
def delete_service(
    service_id: int,
    hard_delete_flag: bool = Query(False, description="Permanently delete (admin only)", alias="hard_delete"),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    service = crud_service.get_service(db, service_id)
    if not service:
        raise HTTPException(status_code=404, detail=_("service_not_found", locale))
    from app.api.core.permissions import check_service_access
    if not check_service_access(db, current_user, service):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    if hard_delete_flag and current_user.role == "admin":
        hard_delete(db, service)
        return {"message": "Service permanently deleted"}
    else:
        soft_delete(db, service)
        return {"message": "Service deleted (soft delete)"}
@router.post("/{service_id}/restore")
def restore_service(
    service_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    service = crud_service.get_service(db, service_id, include_deleted=True)
    if not service:
        raise HTTPException(status_code=404, detail=_("service_not_found", locale))
    from app.api.core.permissions import check_service_access
    if not check_service_access(db, current_user, service):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    restore_soft_deleted(db, service)
    return {"message": "Service restored successfully"}
@router.get("/export/csv")
def export_services_csv(
    specialist_id: Optional[int] = None,
    active_only: bool = False,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
):
    from app.api.core.export import export_to_csv, prepare_service_export_data
    business_id = current_user.business_id if hasattr(current_user, 'business_id') else None
    if current_user.role == "business_owner" and business_id:
        specialist_id = None  # the whole business
    elif current_user.role != "admin":
        specialist_id = current_user.id
    services, _total = crud_service.list_services(
        db,
        specialist_id=specialist_id,
        business_id=business_id,
        # False means "don't filter", not "inactive only"
        is_active=True if active_only else None,
        include_deleted=False,
        include_archived=False,
    )
    export_data = prepare_service_export_data(services)
    return export_to_csv(export_data, filename="services_export.csv")
@router.get("/export/excel")
def export_services_excel(
    specialist_id: Optional[int] = None,
    active_only: bool = False,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
):
    from app.api.core.export import export_to_excel, prepare_service_export_data
    business_id = current_user.business_id if hasattr(current_user, 'business_id') else None
    if current_user.role == "business_owner" and business_id:
        specialist_id = None  # the whole business
    elif current_user.role != "admin":
        specialist_id = current_user.id
    services, _total = crud_service.list_services(
        db,
        specialist_id=specialist_id,
        business_id=business_id,
        # False means "don't filter", not "inactive only"
        is_active=True if active_only else None,
        include_deleted=False,
        include_archived=False,
    )
    export_data = prepare_service_export_data(services)
    return export_to_excel(export_data, filename="services_export.xlsx", sheet_name="Services")
