from typing import Optional, List, Tuple
from datetime import datetime

from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_

from app.api.core.pagination import PaginationParams
from app.api.core.soft_delete import filter_active
from app.db.models.employee import Employee
from app.db.models.user import User
from app.schemas.employee import EmployeeCreate, EmployeeUpdate, EmployeePermissions


def create_employee(
    db: Session,
    employee_in: EmployeeCreate,
    business_owner_id: int
) -> Employee:
    """Создать запись о сотруднике."""
    # Проверяем, что пользователь-сотрудник существует
    employee_user = db.query(User).filter(User.id == employee_in.employee_user_id).first()
    if not employee_user:
        raise ValueError("Employee user not found")
    
    # Проверяем, что пользователь имеет роль employee или specialist
    if employee_user.role not in ["employee", "specialist"]:
        raise ValueError(f"User must have role 'employee' or 'specialist', got '{employee_user.role}'")
    
    # Проверяем, что сотрудник не уже нанят другим бизнесом
    existing_employee = db.query(Employee).filter(
        and_(
            Employee.employee_user_id == employee_in.employee_user_id,
            Employee.is_active == True
        )
    ).first()
    if existing_employee:
        raise ValueError(f"User is already employed by business owner {existing_employee.business_owner_id}")
    
    # Устанавливаем права по умолчанию, если не указаны
    permissions = employee_in.permissions
    if permissions is None:
        default_permissions = EmployeePermissions()
        permissions = default_permissions.model_dump(exclude_none=True)
    
    # Создаем запись Employee
    employee_data = employee_in.model_dump(exclude={"permissions"})
    employee_data["business_owner_id"] = business_owner_id
    employee_data["permissions"] = permissions
    
    employee = Employee(**employee_data)
    
    # Обновляем User: устанавливаем employer_id и hire_date
    employee_user.employer_id = business_owner_id
    employee_user.hire_date = datetime.utcnow()
    if employee_user.role == "specialist":
        # Если это specialist, можно оставить role как есть или изменить на employee
        # Пока оставляем как есть
        pass
    
    db.add(employee)
    db.add(employee_user)
    db.commit()
    db.refresh(employee)
    
    return employee


def get_employee(
    db: Session,
    employee_id: int,
    business_owner_id: Optional[int] = None
) -> Optional[Employee]:
    """Получить информацию о сотруднике."""
    query = db.query(Employee).filter(Employee.id == employee_id)
    
    # Если указан business_owner_id, проверяем доступ
    if business_owner_id is not None:
        query = query.filter(Employee.business_owner_id == business_owner_id)
    
    return query.options(
        joinedload(Employee.employee_user),
        joinedload(Employee.business_owner)
    ).first()


def list_employees(
    db: Session,
    business_owner_id: Optional[int] = None,
    is_active: Optional[bool] = None,
    pagination: Optional[PaginationParams] = None
) -> Tuple[List[Employee], int]:
    """Получить список сотрудников."""
    query = db.query(Employee).options(
        joinedload(Employee.employee_user),
        joinedload(Employee.business_owner)
    )
    
    # Фильтр по владельцу бизнеса
    if business_owner_id is not None:
        query = query.filter(Employee.business_owner_id == business_owner_id)
    
    # Фильтр по активности
    if is_active is not None:
        query = query.filter(Employee.is_active == is_active)
    else:
        # По умолчанию показываем только активных
        query = query.filter(Employee.is_active == True)
    
    # Применяем пагинацию
    total = query.count()
    if pagination:
        query = query.offset(pagination.offset).limit(pagination.limit)
    items = query.all()
    return items, total


def update_employee(
    db: Session,
    employee: Employee,
    employee_update: EmployeeUpdate
) -> Employee:
    """Обновить информацию о сотруднике."""
    update_data = employee_update.model_dump(exclude_unset=True)
    
    # Обработка прав доступа
    if "permissions" in update_data and update_data["permissions"] is None:
        # Если permissions = None, устанавливаем значения по умолчанию
        default_permissions = EmployeePermissions()
        update_data["permissions"] = default_permissions.model_dump()
    
    for field, value in update_data.items():
        setattr(employee, field, value)
    
    employee.updated_at = datetime.utcnow()
    
    db.add(employee)
    db.commit()
    db.refresh(employee)
    
    return employee


def fire_employee(
    db: Session,
    employee: Employee
) -> Employee:
    """Уволить сотрудника (soft delete)."""
    employee.is_active = False
    employee.fired_at = datetime.utcnow()
    employee.updated_at = datetime.utcnow()
    
    # Обновляем User: убираем employer_id
    employee_user = employee.employee_user
    if employee_user:
        employee_user.employer_id = None
        db.add(employee_user)
    
    db.add(employee)
    db.commit()
    db.refresh(employee)
    
    return employee


def get_employee_by_user_id(
    db: Session,
    user_id: int,
    business_owner_id: Optional[int] = None
) -> Optional[Employee]:
    """Получить запись Employee по user_id сотрудника."""
    query = db.query(Employee).filter(Employee.employee_user_id == user_id)
    
    if business_owner_id is not None:
        query = query.filter(Employee.business_owner_id == business_owner_id)
    
    return query.options(
        joinedload(Employee.employee_user),
        joinedload(Employee.business_owner)
    ).first()


def get_employees_by_business_owner(
    db: Session,
    business_owner_id: int,
    active_only: bool = True
) -> List[Employee]:
    """Получить всех сотрудников бизнес-владельца."""
    query = db.query(Employee).filter(Employee.business_owner_id == business_owner_id)
    
    if active_only:
        query = query.filter(Employee.is_active == True)
    
    return query.options(
        joinedload(Employee.employee_user)
    ).all()

