from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core.i18n import _, localize_response
from app.api.core.pagination import PaginationParams, PaginatedResponse
from app.api.crud import crud_service
from app.db.models.user import User
from app.schemas.service import ServiceCreate, ServiceOut, ServiceUpdate

router = APIRouter()


@router.post("/", response_model=ServiceOut)
def create_service(
    service: ServiceCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
    locale: str = Depends(deps.get_locale),
):
    if current_user.role != "admin" and service.specialist_id != current_user.id:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    # Получаем business_id текущего пользователя
    business_id = current_user.business_id if hasattr(current_user, 'business_id') else None
    result = crud_service.create_service(db, service, business_id=business_id)
    return localize_response(result.dict() if hasattr(result, 'dict') else result, locale)


@router.get("/", response_model=PaginatedResponse[ServiceOut])
def list_services(
    specialist_id: Optional[int] = None,
    active_only: bool = False,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
    locale: str = Depends(deps.get_locale),
):
    # Получаем business_id текущего пользователя для изоляции данных
    business_id = current_user.business_id if hasattr(current_user, 'business_id') else None
    
    # Устанавливаем specialist_id только если он не указан в query параметрах И пользователь не админ
    if current_user.role != "admin" and specialist_id is None:
        specialist_id = current_user.id
    
    pagination = PaginationParams(page=page, per_page=per_page)
    services, total = crud_service.list_services(
        db, 
        specialist_id=specialist_id, 
        business_id=business_id,  # Изоляция по бизнесу
        is_active=active_only,
        pagination=pagination
    )
    
    localized_items = [
        localize_response(s.dict() if hasattr(s, 'dict') else s, locale) for s in services
    ]
    
    return PaginatedResponse.create(
        items=localized_items,
        total=total,
        page=page,
        per_page=per_page,
    )


@router.put("/{service_id}", response_model=ServiceOut)
def update_service(
    service_id: int,
    payload: ServiceUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
    locale: str = Depends(deps.get_locale),
):
    service = crud_service.get_service(db, service_id)
    if not service:
        raise HTTPException(status_code=404, detail=_("service_not_found", locale))
    if current_user.role != "admin" and service.specialist_id != current_user.id:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    result = crud_service.update_service(db, service, payload)
    return localize_response(result.dict() if hasattr(result, 'dict') else result, locale)


@router.delete("/{service_id}")
def delete_service(
    service_id: int,
    hard_delete_flag: bool = Query(False, description="Permanently delete (admin only)", alias="hard_delete"),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
    locale: str = Depends(deps.get_locale),
):
    """Delete service (soft delete by default, hard delete if hard_delete=True and user is admin)."""
    service = crud_service.get_service(db, service_id)
    if not service:
        raise HTTPException(status_code=404, detail=_("service_not_found", locale))
    if current_user.role != "admin" and service.specialist_id != current_user.id:
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
    current_user: User = Depends(deps.role_required("admin", "specialist")),
    locale: str = Depends(deps.get_locale),
):
    """Restore a soft-deleted service."""
    service = crud_service.get_service(db, service_id, include_deleted=True)
    if not service:
        raise HTTPException(status_code=404, detail=_("service_not_found", locale))
    if current_user.role != "admin" and service.specialist_id != current_user.id:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    
    restore_soft_deleted(db, service)
    return {"message": "Service restored successfully"}


@router.get("/export/csv")
def export_services_csv(
    specialist_id: Optional[int] = None,
    active_only: bool = False,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
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
    current_user: User = Depends(deps.role_required("admin", "specialist")),
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

