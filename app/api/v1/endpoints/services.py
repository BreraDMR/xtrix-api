from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core.i18n import _, localize_response
from app.api.core.pagination import PaginationParams, PaginatedResponse
from app.api.crud import crud_service
from app.db.models.service import Service
from app.db.models.user import User
from app.schemas.service import ServiceCreate, ServiceOut, ServiceUpdate

router = APIRouter()


@router.post("/", response_model=ServiceOut)
def create_service(
    service: ServiceCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "business_owner", "specialist", "employee")),
    locale: str = Depends(deps.get_locale),
):
    from app.api.core.permissions import can_manage_services
    
    # Проверяем право на управление услугами
    if not can_manage_services(current_user, db):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    
    # Устанавливаем specialist_id, если не указан (для employee)
    if service.specialist_id is None or service.specialist_id == 0:
        service.specialist_id = current_user.id
    elif current_user.role not in ["admin", "business_owner"]:
        # specialist и employee могут создавать услуги только для себя
        if service.specialist_id != current_user.id:
            raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    
    # Получаем business_id текущего пользователя
    business_id = current_user.business_id if hasattr(current_user, 'business_id') else None
    result = crud_service.create_service(db, service, business_id=business_id)
    
    # Перезагружаем с информацией о специалистах
    result = crud_service.get_service(db, result.id)
    
    # Преобразуем в ServiceOut
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
    # Получаем business_id текущего пользователя для изоляции данных
    business_id = current_user.business_id if hasattr(current_user, 'business_id') else None
    
    from app.api.core.permissions import get_filtered_specialist_id, can_manage_services
    
    # Проверяем право на просмотр услуг
    if not can_manage_services(current_user, db):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    
    # Фильтрация по ролям
    employee_specialist_ids = None  # ID специалистов-сотрудников админа/владельца
    if current_user.role == "admin":
        # Админ видит услуги своего бизнеса + услуги всех своих сотрудников (специалистов)
        if business_id:
            # Получаем список всех специалистов, которые являются сотрудниками админа
            from app.db.models.employee import Employee
            from app.api.core.permissions import get_business_owner_ids_for_filter
            owner_ids = get_business_owner_ids_for_filter(current_user, db) or [current_user.id]
            employees = db.query(Employee.employee_user_id).filter(
                Employee.business_owner_id.in_(owner_ids),
                Employee.is_active == True
            ).all()
            employee_specialist_ids = [emp[0] for emp in employees] if employees else []
            # Если есть сотрудники, не фильтруем по specialist_id в основном запросе
            # но передадим их ID для дополнительной фильтрации
        # Админ может также фильтровать по specialist_id, если указан
        if specialist_id:
            # Если указан конкретный specialist_id, используем его
            pass
        else:
            # Если не указан, показываем все услуги бизнеса + услуги сотрудников
            specialist_id = None
    elif current_user.role == "business_owner":
        # Владелец бизнеса видит все услуги бизнеса + услуги своих сотрудников
        from app.db.models.employee import Employee
        employees = db.query(Employee.employee_user_id).filter(
            Employee.business_owner_id == current_user.id,
            Employee.is_active == True
        ).all()
        employee_specialist_ids = [emp[0] for emp in employees] if employees else []
        specialist_id = None  # Не фильтруем по specialist_id
    else:
        # specialist и employee видят только свои услуги
        if specialist_id is None:
            specialist_id = get_filtered_specialist_id(current_user)
    
    pagination = PaginationParams(page=page, per_page=per_page)
    # active_only=True означает фильтровать только активные, False/None означает показать все
    is_active_filter = True if active_only else None
    services, total = crud_service.list_services(
        db, 
        specialist_id=specialist_id, 
        business_id=business_id,  # Изоляция по бизнесу
        employee_specialist_ids=employee_specialist_ids,  # Дополнительная фильтрация по сотрудникам
        is_active=is_active_filter,
        pagination=pagination
    )
    
    # Преобразуем в ServiceOut с информацией о специалистах
    def service_to_dict(service: Service):
        result = service.dict() if hasattr(service, 'dict') else service.__dict__.copy()
        # Получаем список ID специалистов из service_specialists
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
    """Get service by ID."""
    service = crud_service.get_service(db, service_id)
    if not service:
        raise HTTPException(status_code=404, detail=_("service_not_found", locale))
    
    # Проверяем доступ используя новую систему прав
    from app.api.core.permissions import check_service_access
    if not check_service_access(db, current_user, service):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    
    # Преобразуем в ServiceOut
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
    # Проверяем доступ используя новую систему прав
    from app.api.core.permissions import check_service_access
    if not check_service_access(db, current_user, service):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    result = crud_service.update_service(db, service, payload)
    
    # Перезагружаем с информацией о специалистах
    result = crud_service.get_service(db, result.id)
    
    # Преобразуем в ServiceOut
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
    """Delete service (soft delete by default, hard delete if hard_delete=True and user is admin)."""
    service = crud_service.get_service(db, service_id)
    if not service:
        raise HTTPException(status_code=404, detail=_("service_not_found", locale))
    # Проверяем доступ используя новую систему прав
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
    """Restore a soft-deleted service."""
    service = crud_service.get_service(db, service_id, include_deleted=True)
    if not service:
        raise HTTPException(status_code=404, detail=_("service_not_found", locale))
    # Проверяем доступ используя новую систему прав
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
    """Export services to CSV."""
    from app.api.core.export import export_to_csv, prepare_service_export_data
    
    # Получаем business_id текущего пользователя для изоляции данных
    business_id = current_user.business_id if hasattr(current_user, 'business_id') else None
    
    if current_user.role != "admin":
        specialist_id = current_user.id
    
    services, _ = crud_service.list_services(
        db,
        specialist_id=specialist_id,
        business_id=business_id,  # Изоляция по бизнесу
        is_active=active_only,
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
    """Export services to Excel."""
    from app.api.core.export import export_to_excel, prepare_service_export_data
    
    # Получаем business_id текущего пользователя для изоляции данных
    business_id = current_user.business_id if hasattr(current_user, 'business_id') else None
    
    if current_user.role != "admin":
        specialist_id = current_user.id
    
    services, _ = crud_service.list_services(
        db,
        specialist_id=specialist_id,
        business_id=business_id,  # Изоляция по бизнесу
        is_active=active_only,
        include_deleted=False,
        include_archived=False,
    )
    export_data = prepare_service_export_data(services)
    return export_to_excel(export_data, filename="services_export.xlsx", sheet_name="Services")

