"""Employee management endpoints."""
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core.i18n import _
from app.api.core.pagination import PaginationParams, create_paginated_response
from app.api.crud import crud_employee
from app.db.models.user import User
from app.db.models.employee import Employee
from app.schemas.employee import EmployeeCreate, EmployeeUpdate, EmployeeOut

router = APIRouter()


def can_manage_employees(current_user: User) -> bool:
    """Проверяет, может ли пользователь управлять сотрудниками."""
    return current_user.role in ["admin", "business_owner"]


def get_business_owner_id(current_user: User) -> int:
    """Возвращает ID владельца бизнеса для проверки доступа."""
    if current_user.role == "admin":
        # Админ может управлять сотрудниками любого бизнеса
        # В этом случае business_owner_id должен быть указан в параметрах
        return None  # None означает "любой"
    
    if current_user.role == "business_owner":
        return current_user.id
    
    raise HTTPException(status_code=403, detail=_("not_allowed", "en"))


@router.post("/", response_model=EmployeeOut, summary="Hire employee")
def create_employee(
    employee: EmployeeCreate,
    business_owner_id: Optional[int] = Query(None, description="Business owner ID (required for admin)"),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    """Нанять сотрудника. Только business_owner или admin."""
    if not can_manage_employees(current_user):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    
    # Определяем business_owner_id
    if current_user.role == "admin":
        # Админ должен указать business_owner_id
        if not business_owner_id:
            raise HTTPException(
                status_code=400, 
                detail=_("business_owner_id parameter is required for admin", locale)
            )
        owner_id = business_owner_id
    else:
        # business_owner использует свой ID
        owner_id = current_user.id
    
    try:
        new_employee = crud_employee.create_employee(
            db=db,
            employee_in=employee,
            business_owner_id=owner_id
        )
        return new_employee
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/", response_model=List[EmployeeOut], summary="List employees")
def list_employees(
    business_owner_id: Optional[int] = Query(None, description="Filter by business owner (admin only)"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    """Получить список сотрудников."""
    if not can_manage_employees(current_user):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    
    # Определяем business_owner_id для фильтрации
    owner_id = get_business_owner_id(current_user)
    if current_user.role == "admin":
        # Админ может фильтровать по любому business_owner_id
        owner_id = business_owner_id
    else:
        # business_owner может видеть только своих сотрудников
        owner_id = current_user.id
    
    pagination = PaginationParams(page=page, per_page=per_page)
    employees, total = crud_employee.list_employees(
        db=db,
        business_owner_id=owner_id,
        is_active=is_active,
        pagination=pagination
    )
    
    return employees


@router.get("/{employee_id}", response_model=EmployeeOut, summary="Get employee")
def get_employee(
    employee_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    """Получить информацию о сотруднике."""
    if not can_manage_employees(current_user):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    
    owner_id = get_business_owner_id(current_user)
    if current_user.role == "admin":
        owner_id = None  # Админ может видеть любых сотрудников
    
    employee = crud_employee.get_employee(
        db=db,
        employee_id=employee_id,
        business_owner_id=owner_id
    )
    
    if not employee:
        raise HTTPException(status_code=404, detail=_("employee_not_found", locale))
    
    return employee


@router.put("/{employee_id}", response_model=EmployeeOut, summary="Update employee")
def update_employee(
    employee_id: int,
    employee_update: EmployeeUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    """Обновить информацию о сотруднике."""
    if not can_manage_employees(current_user):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    
    owner_id = get_business_owner_id(current_user)
    if current_user.role == "admin":
        owner_id = None
    
    employee = crud_employee.get_employee(
        db=db,
        employee_id=employee_id,
        business_owner_id=owner_id
    )
    
    if not employee:
        raise HTTPException(status_code=404, detail=_("employee_not_found", locale))
    
    updated_employee = crud_employee.update_employee(
        db=db,
        employee=employee,
        employee_update=employee_update
    )
    
    return updated_employee


@router.delete("/{employee_id}", response_model=EmployeeOut, summary="Fire employee")
def fire_employee(
    employee_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    """Уволить сотрудника (soft delete)."""
    if not can_manage_employees(current_user):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    
    owner_id = get_business_owner_id(current_user)
    if current_user.role == "admin":
        owner_id = None
    
    employee = crud_employee.get_employee(
        db=db,
        employee_id=employee_id,
        business_owner_id=owner_id
    )
    
    if not employee:
        raise HTTPException(status_code=404, detail=_("employee_not_found", locale))
    
    if not employee.is_active:
        raise HTTPException(status_code=400, detail=_("employee_already_fired", locale))
    
    fired_employee = crud_employee.fire_employee(db=db, employee=employee)
    
    return fired_employee


@router.get("/by-user/{user_id}", response_model=EmployeeOut, summary="Get employee by user ID")
def get_employee_by_user_id(
    user_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    """Получить информацию о сотруднике по user_id."""
    # Специалист может видеть свою запись employee
    if current_user.role == "employee" and current_user.id != user_id:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    
    # Проверяем доступ
    owner_id = None
    if current_user.role == "business_owner":
        owner_id = current_user.id
    elif current_user.role == "admin":
        owner_id = None
    
    employee = crud_employee.get_employee_by_user_id(
        db=db,
        user_id=user_id,
        business_owner_id=owner_id
    )
    
    if not employee:
        raise HTTPException(status_code=404, detail=_("employee_not_found", locale))
    
    return employee

