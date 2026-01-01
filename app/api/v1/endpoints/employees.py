"""Employee management endpoints."""
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core.i18n import _
from app.api.core.pagination import PaginationParams, PaginatedResponse
from app.api.crud import crud_employee, crud_salary
from app.db.models.user import User
from app.db.models.employee import Employee
from app.schemas.employee import EmployeeCreate, EmployeeUpdate, EmployeeOut
from app.schemas.salary import (
    SalaryCalculationOut, 
    SalaryCalculationCreate, 
    SalaryCalculationUpdate,
    CalculateSalaryRequest
)

router = APIRouter()


def can_manage_employees(current_user: User) -> bool:
    """Проверяет, может ли пользователь управлять сотрудниками."""
    return current_user.role in ["admin", "business_owner"]


def get_business_owner_ids_for_filter(current_user: User, db: Session) -> Optional[List[int]]:
    """
    Возвращает список business_owner_id для фильтрации данных.
    Для админа возвращает список всех business_owner_id его бизнеса.
    Для business_owner возвращает список с одним элементом - его ID.
    """
    if current_user.role == "admin":
        # Админ видит только данные своего бизнеса
        admin_business_id = current_user.business_id if hasattr(current_user, 'business_id') else None
        if admin_business_id:
            # Находим всех business_owners с таким же business_id
            from app.db.models.user import User
            business_owners = db.query(User.id).filter(
                User.role == "business_owner",
                User.business_id == admin_business_id
            ).all()
            return [bo[0] for bo in business_owners]
        return []  # У админа нет business_id - нет данных
    
    if current_user.role == "business_owner":
        return [current_user.id]
    
    return None


def get_business_owner_id(current_user: User) -> Optional[int]:
    """Возвращает ID владельца бизнеса для проверки доступа (legacy функция)."""
    if current_user.role == "business_owner":
        return current_user.id
    return None


@router.post("/", response_model=EmployeeOut, summary="Hire employee")
def create_employee(
    employee: EmployeeCreate,
    business_owner_id: Optional[int] = Query(None, description="Business owner ID (optional, for admin)"),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    """Нанять сотрудника. Только business_owner или admin."""
    if not can_manage_employees(current_user):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    
    # Определяем business_owner_id
    if current_user.role == "admin":
        # Админ может указать business_owner_id в теле запроса или в query параметре
        owner_id = employee.business_owner_id or business_owner_id
        if not owner_id:
            # Если не указано, используем business_id текущего пользователя (если есть)
            # или требуем явно указать
            if current_user.business_id:
                # Получаем владельца бизнеса через business_id (ищем User с role=business_owner и этим business_id)
                from app.db.models.user import User
                business_owner = db.query(User).filter(
                    User.business_id == current_user.business_id,
                    User.role == "business_owner"
                ).first()
                if business_owner:
                    owner_id = business_owner.id
            # Если все еще не определено, пытаемся использовать ID самого админа как business_owner_id
            # (это может быть случай, когда админ также является владельцем бизнеса)
            if not owner_id:
                # Проверяем, есть ли у админа связанный бизнес через relationship
                # Если админ сам является владельцем бизнеса, используем его ID
                owner_id = current_user.id
    elif current_user.role == "business_owner":
        # business_owner использует свой ID (игнорируем переданный business_owner_id)
        owner_id = current_user.id
    else:
        # Другие роли не могут создавать сотрудников
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    
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
    business_owner_ids = None  # Для фильтрации по списку
    
    if current_user.role == "admin":
        # Админ видит только сотрудников своего бизнеса (изоляция данных)
        admin_business_id = current_user.business_id if hasattr(current_user, 'business_id') else None
        if admin_business_id:
            # Находим всех business_owners с таким же business_id
            from app.db.models.user import User
            business_owners = db.query(User.id).filter(
                User.role == "business_owner",
                User.business_id == admin_business_id
            ).all()
            owner_ids = [bo[0] for bo in business_owners]
            if owner_ids:
                # Фильтруем по списку business_owner_id из этого бизнеса
                business_owner_ids = owner_ids
            else:
                # Нет business_owners в этом бизнесе - пустой список
                return []
        else:
            # У админа нет business_id - пустой список (новый аккаунт без данных)
            return []
        owner_id = None  # Не используем единичный business_owner_id для админа
    else:
        owner_id = get_business_owner_id(current_user)
        # business_owner может видеть только своих сотрудников
        if current_user.role != "admin":
            owner_id = current_user.id
    
    pagination = PaginationParams(page=page, per_page=per_page)
    employees, total = crud_employee.list_employees(
        db=db,
        business_owner_id=owner_id,
        business_owner_ids=business_owner_ids,
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


# ==================== Salary Management Endpoints ====================

@router.post("/{employee_id}/salary/calculate", response_model=SalaryCalculationOut, summary="Calculate salary for employee")
def calculate_employee_salary(
    employee_id: int,
    request: CalculateSalaryRequest,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    """Рассчитать зарплату сотрудника за период. Только business_owner или admin."""
    from app.api.core.permissions import can_manage_employees
    
    if not can_manage_employees(current_user):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    
    # Проверяем доступ к сотруднику
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
        raise HTTPException(status_code=400, detail=_("Cannot calculate salary for inactive employee", locale))
    
    # Проверяем, что период валиден
    if request.period_start >= request.period_end:
        raise HTTPException(status_code=400, detail=_("period_start must be before period_end", locale))
    
    try:
        salary_calculation = crud_salary.calculate_salary(
            db=db,
            employee_id=employee_id,
            period_start=request.period_start,
            period_end=request.period_end,
            calculated_by_id=current_user.id,
            notes=request.notes
        )
        return salary_calculation
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{employee_id}/salary/current", response_model=SalaryCalculationOut, summary="Get current unpaid salary for employee")
def get_employee_current_salary(
    employee_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    """Получить текущую невыплаченную зарплату сотрудника."""
    from app.api.core.permissions import can_manage_employees
    
    # Проверяем доступ
    if current_user.role == "employee":
        # Сотрудник может видеть только свои зарплаты
        employee = crud_employee.get_employee_by_user_id(db, current_user.id)
        if not employee or employee.id != employee_id:
            raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    elif can_manage_employees(current_user):
        # business_owner или admin
        owner_id = get_business_owner_id(current_user)
        if current_user.role == "admin":
            owner_id = None
        
        # Проверяем доступ к сотруднику
        employee = crud_employee.get_employee(
            db=db,
            employee_id=employee_id,
            business_owner_id=owner_id
        )
        if not employee:
            raise HTTPException(status_code=404, detail=_("employee_not_found", locale))
    else:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    
    salary = crud_salary.get_current_unpaid_salary(db=db, employee_id=employee_id)
    if not salary:
        raise HTTPException(status_code=404, detail=_("no_unpaid_salary", locale))
    
    return salary


@router.get("/{employee_id}/salary/history", response_model=List[SalaryCalculationOut], summary="Get salary history for employee")
def get_employee_salary_history(
    employee_id: int,
    is_paid: Optional[bool] = Query(None, description="Filter by paid status"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    """Получить историю зарплат сотрудника."""
    from app.api.core.permissions import can_manage_employees
    
    # Проверяем доступ
    if current_user.role == "employee":
        # Сотрудник может видеть только свои зарплаты
        employee = crud_employee.get_employee_by_user_id(db, current_user.id)
        if not employee or employee.id != employee_id:
            raise HTTPException(status_code=403, detail=_("not_allowed", locale))
        owner_id = None
    elif can_manage_employees(current_user):
        # business_owner или admin
        owner_id = get_business_owner_id(current_user)
        if current_user.role == "admin":
            owner_id = None
        
        # Проверяем доступ к сотруднику
        employee = crud_employee.get_employee(
            db=db,
            employee_id=employee_id,
            business_owner_id=owner_id
        )
        if not employee:
            raise HTTPException(status_code=404, detail=_("employee_not_found", locale))
    else:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    
    pagination = PaginationParams(page=page, per_page=per_page)
    salaries, total = crud_salary.list_salary_calculations(
        db=db,
        employee_id=employee_id,
        is_paid=is_paid,
        pagination=pagination
    )
    
    return salaries


@router.get("/{employee_id}/salary/{salary_id}", response_model=SalaryCalculationOut, summary="Get salary calculation details")
def get_salary_calculation(
    employee_id: int,
    salary_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    """Получить детали расчета зарплаты."""
    from app.api.core.permissions import can_manage_employees
    
    # Проверяем доступ
    if current_user.role == "employee":
        employee = crud_employee.get_employee_by_user_id(db, current_user.id)
        if not employee or employee.id != employee_id:
            raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    elif can_manage_employees(current_user):
        owner_id = get_business_owner_id(current_user)
        if current_user.role == "admin":
            owner_id = None
        employee = crud_employee.get_employee(db=db, employee_id=employee_id, business_owner_id=owner_id)
        if not employee:
            raise HTTPException(status_code=404, detail=_("employee_not_found", locale))
    else:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    
    salary = crud_salary.get_salary_calculation(db=db, salary_id=salary_id, employee_id=employee_id)
    if not salary:
        raise HTTPException(status_code=404, detail=_("salary_calculation_not_found", locale))
    
    return salary


@router.put("/{employee_id}/salary/{salary_id}", response_model=SalaryCalculationOut, summary="Update salary calculation (mark as paid)")
def update_salary_calculation(
    employee_id: int,
    salary_id: int,
    update: SalaryCalculationUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    """Обновить расчет зарплаты (например, отметить как выплаченную). Только business_owner или admin."""
    from app.api.core.permissions import can_manage_employees
    
    if not can_manage_employees(current_user):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    
    # Проверяем доступ к сотруднику
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
    
    salary = crud_salary.get_salary_calculation(db=db, salary_id=salary_id, employee_id=employee_id)
    if not salary:
        raise HTTPException(status_code=404, detail=_("salary_calculation_not_found", locale))
    
    updated_salary = crud_salary.update_salary_calculation(db=db, salary_calculation=salary, update=update)
    
    return updated_salary


# Endpoints для сотрудника (просмотр своих зарплат)

@router.get("/me/salary/current", response_model=SalaryCalculationOut, summary="Get current unpaid salary")
def get_my_current_salary(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    """Получить текущую невыплаченную зарплату. Только для employee."""
    if current_user.role != "employee":
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    
    salary = crud_salary.get_current_unpaid_salary(db=db, employee_user_id=current_user.id)
    if not salary:
        raise HTTPException(status_code=404, detail=_("no_unpaid_salary", locale))
    
    return salary


@router.get("/me/salary/history", response_model=List[SalaryCalculationOut], summary="Get my salary history")
def get_my_salary_history(
    limit: int = Query(10, ge=1, le=100, description="Maximum number of records to return"),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    """Получить историю моих зарплат. Только для employee."""
    if current_user.role != "employee":
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    
    salaries = crud_salary.get_salary_history(db=db, employee_user_id=current_user.id, limit=limit)
    return salaries

