"""Employee management endpoints."""
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core.i18n import _
from app.api.core.logger import get_logger
from app.api.core.pagination import PaginationParams, PaginatedResponse
from app.api.crud import crud_employee, crud_salary
from app.db.models.user import User
from app.db.models.employee import Employee
from app.schemas.employee import EmployeeCreate, EmployeeUpdate, EmployeeOut, EmployeeCreateWithUser, EmployeeCreateResponse
from app.schemas.salary import (
    SalaryCalculationOut, 
    SalaryCalculationCreate, 
    SalaryCalculationUpdate,
    CalculateSalaryRequest
)

router = APIRouter()
logger = get_logger("employees")


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


@router.post("/create-with-user", response_model=EmployeeCreateResponse, summary="Create employee with new user")
def create_employee_with_user(
    employee_data: EmployeeCreateWithUser,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    """
    Создать нового сотрудника с автоматическим созданием пользователя.
    Генерируется пароль, который возвращается в ответе для передачи сотруднику.
    """
    if not can_manage_employees(current_user):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    
    # Проверяем наличие email или phone
    if not employee_data.email and not employee_data.phone:
        raise HTTPException(
            status_code=400,
            detail=_("email_or_phone_required", locale) or "Email or phone number is required"
        )
    
    # Проверяем, что пользователь с таким email/phone не существует
    from app.api.crud import crud_user
    existing_user = crud_user.get_by_phone_or_email(db, phone=employee_data.phone, email=employee_data.email)
    if existing_user:
        raise HTTPException(
            status_code=400,
            detail=_("user_already_exists", locale) or f"User with this email/phone already exists (ID: {existing_user.id})"
        )
    
    # Определяем business_owner_id и business_id
    business_owner_id = None
    business_id = employee_data.business_id
    
    if current_user.role == "admin":
        # Админ использует business_id текущего админа
        if current_user.business_id:
            business_id = current_user.business_id
            # Находим business_owner с таким business_id
            from app.db.models.user import User
            business_owner = db.query(User).filter(
                User.business_id == current_user.business_id,
                User.role == "business_owner"
            ).first()
            if business_owner:
                business_owner_id = business_owner.id
            else:
                # Если нет business_owner, используем админа как business_owner_id
                # Это означает, что админ сам управляет сотрудниками напрямую
                business_owner_id = current_user.id
        else:
            # Если у админа нет business_id, все равно можно создать сотрудника
            # Используем админа как business_owner_id
            business_owner_id = current_user.id
            business_id = None  # business_id будет установлен автоматически, если у админа он есть
    elif current_user.role == "business_owner":
        business_owner_id = current_user.id
        business_id = current_user.business_id if hasattr(current_user, 'business_id') else None
    
    if not business_owner_id:
        raise HTTPException(
            status_code=400,
            detail=_("business_owner_id_required", locale) or "Business owner ID is required"
        )
    
    # Генерируем безопасный пароль
    from app.api.core.security import generate_secure_password
    generated_password = generate_secure_password(12)
    
    # Валидируем телефон заранее, чтобы обработать ошибки
    phone_for_validation = employee_data.phone
    if phone_for_validation:
        from app.api.core.security import validate_phone
        is_valid, error_msg = validate_phone(phone_for_validation)
        if not is_valid:
            if employee_data.email:
                # Если телефон невалидный, но есть email - используем только email
                phone_for_validation = None
            else:
                # Если телефон невалидный и нет email - ошибка
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid phone number: {error_msg}. Please provide a valid phone number (minimum 7 digits after country code) or an email address."
                )
    
    # Проверяем, что хотя бы один из способов связи указан
    if not phone_for_validation and not employee_data.email:
        raise HTTPException(
            status_code=400,
            detail="Either a valid phone number or email address is required"
        )
    
    # Создаем пользователя
    from app.schemas.user import UserCreate
    from pydantic import ValidationError
    
    try:
        user_create = UserCreate(
            full_name=employee_data.full_name,
            phone=phone_for_validation,
            email=employee_data.email,
            password=generated_password,
            role="specialist",  # Всегда создаем как specialist
            specialist_type=employee_data.specialist_type,
            business_id=business_id,  # Привязываем к бизнесу админа
        )
    except ValidationError as e:
        # Обрабатываем ошибки валидации Pydantic
        errors = []
        for error in e.errors():
            field = ".".join(str(loc) for loc in error["loc"])
            msg = error["msg"]
            errors.append(f"{field}: {msg}")
        
        raise HTTPException(
            status_code=400,
            detail=f"Validation error: {'; '.join(errors)}"
        )
    
    try:
        new_user = crud_user.create_user(db, user_create)
    except Exception as e:
        # Обрабатываем другие ошибки (например, дубликаты)
        error_msg = str(e)
        if "already exists" in error_msg.lower() or "unique" in error_msg.lower():
            raise HTTPException(
                status_code=400,
                detail="User with this email or phone already exists"
            )
        raise HTTPException(
            status_code=400,
            detail=f"Failed to create user: {error_msg}"
        )
    
    # Создаем запись Employee
    from app.schemas.employee import EmployeeCreate
    employee_create = EmployeeCreate(
        employee_user_id=new_user.id,
        position=employee_data.position,
        permissions=employee_data.permissions,
        commission_percentage=employee_data.commission_percentage,
        base_salary=employee_data.base_salary,
        notes=employee_data.notes,
        business_owner_id=business_owner_id,
    )
    
    # Логируем перед созданием сотрудника
    logger.info(f"Creating employee: user_id={new_user.id}, business_owner_id={business_owner_id}, "
                f"admin_id={current_user.id}, business_id={business_id}")
    
    new_employee = crud_employee.create_employee(db, employee_create, business_owner_id=business_owner_id)
    
    # Логируем после создания
    logger.info(f"Employee created: employee_id={new_employee.id}, employee_user_id={new_employee.employee_user_id}, "
                f"business_owner_id={new_employee.business_owner_id}, is_active={new_employee.is_active}")
    
    # Автоматически создаем SpecialistProfile для нового специалиста, если его еще нет
    from app.api.crud import crud_specialist_profile
    from app.schemas.specialist_profile import SpecialistProfileCreate
    try:
        existing_profile = crud_specialist_profile.get_specialist_profile_by_user_id(db, new_user.id)
        if not existing_profile:
            # Определяем категорию на основе типа специалиста
            specialist_category = None
            if employee_data.specialist_type:
                from app.db.models.specialist_type import SpecialistType, SpecialistCategory, get_category_for_type
                try:
                    specialist_type_enum = SpecialistType(employee_data.specialist_type)
                    specialist_category = get_category_for_type(specialist_type_enum).value
                except (ValueError, AttributeError):
                    # Если не удалось определить категорию, оставляем None
                    pass
            
            profile_create = SpecialistProfileCreate(
                user_id=new_user.id,
                specialist_type=employee_data.specialist_type,
                specialist_category=specialist_category,
            )
            new_profile = crud_specialist_profile.create_specialist_profile(db, profile_create)
            
            # Автоматически верифицируем профиль, так как админ создал сотрудника
            from datetime import datetime
            new_profile.is_verified = "verified"
            new_profile.verified_at = datetime.utcnow()
            db.add(new_profile)
            db.commit()
            logger.info(f"Auto-verified specialist profile for user {new_user.id} (created by admin)")
    except Exception as e:
        # Если не удалось создать профиль, не останавливаем процесс создания сотрудника
        # Просто логируем ошибку (профиль можно создать позже)
        logger.warning(f"Failed to auto-create specialist profile for user {new_user.id}: {str(e)}")
    
    # Формируем ответ с паролем
    response_data = {
        "id": new_employee.id,
        "business_owner_id": new_employee.business_owner_id,
        "employee_user_id": new_employee.employee_user_id,
        "position": new_employee.position,
        "permissions": new_employee.permissions,
        "commission_percentage": new_employee.commission_percentage or 0.0,
        "base_salary": new_employee.base_salary or 0.0,
        "is_active": new_employee.is_active,
        "notes": new_employee.notes,
        "hired_at": new_employee.hired_at,
        "fired_at": new_employee.fired_at,
        "generated_password": generated_password,
        "user_id": new_user.id,
        "employee_user": {
            "id": new_user.id,
            "full_name": new_user.full_name,
            "email": new_user.email,
            "phone": new_user.phone,
            "role": new_user.role,
        }
    }
    
    return EmployeeCreateResponse(**response_data)


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
        
        # Упрощенная логика: если у админа есть business_id, показываем всех сотрудников бизнеса
        # Включая сотрудников с business_owner_id = админ.id
        owner_ids = []
        
        if admin_business_id:
            # Находим всех business_owners с таким же business_id
            from app.db.models.user import User
            business_owners = db.query(User.id).filter(
                User.role == "business_owner",
                User.business_id == admin_business_id
            ).all()
            owner_ids = [bo[0] for bo in business_owners]
            
            # ВСЕГДА добавляем ID админа (он может создавать сотрудников напрямую)
            if current_user.id not in owner_ids:
                owner_ids.append(current_user.id)
            
            if len(owner_ids) > 0:
                business_owner_ids = owner_ids
            else:
                # Не должно произойти, так как мы добавили админа, но на всякий случай
                business_owner_ids = [current_user.id]
        else:
            # У админа нет business_id - показываем только его собственных сотрудников
            business_owner_ids = [current_user.id]
        
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
    
    # Логируем для отладки
    logger.info(f"List employees: current_user_id={current_user.id}, role={current_user.role}, "
                f"business_id={getattr(current_user, 'business_id', None)}, "
                f"business_owner_ids={business_owner_ids}, owner_id={owner_id}, "
                f"is_active={is_active}, found {len(employees)} employees (total: {total})")
    
    # Преобразуем объекты Employee в формат EmployeeOut
    # Нужно преобразовать employee_user из объекта User в словарь
    result = []
    for emp in employees:
        emp_dict = {
            "id": emp.id,
            "employee_user_id": emp.employee_user_id,
            "business_owner_id": emp.business_owner_id,
            "position": emp.position,
            "permissions": emp.permissions,
            "commission_percentage": emp.commission_percentage or 0.0,
            "base_salary": emp.base_salary or 0.0,
            "is_active": emp.is_active,
            "notes": emp.notes,
            "hired_at": emp.hired_at,
            "fired_at": emp.fired_at,
            "employee_user": None
        }
        
        # Преобразуем employee_user в словарь, если он загружен
        if emp.employee_user:
            emp_dict["employee_user"] = {
                "id": emp.employee_user.id,
                "full_name": emp.employee_user.full_name,
                "email": emp.employee_user.email,
                "phone": emp.employee_user.phone,
                "role": emp.employee_user.role,
                "specialist_type": getattr(emp.employee_user, 'specialist_type', None),
                "business_id": getattr(emp.employee_user, 'business_id', None),
            }
        
        result.append(EmployeeOut(**emp_dict))
    
    return result


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
    
    owner_id = None
    admin_business_id = None
    owner_ids = []
    
    if current_user.role == "admin":
        # Админ может видеть сотрудников своего бизнеса
        # Проверяем доступ через business_id
        admin_business_id = current_user.business_id if hasattr(current_user, 'business_id') else None
        if admin_business_id:
            # Находим всех business_owners с таким же business_id
            from app.db.models.user import User
            business_owners = db.query(User.id).filter(
                User.role == "business_owner",
                User.business_id == admin_business_id
            ).all()
            owner_ids = [bo[0] for bo in business_owners]
            # Добавляем админа
            if current_user.id not in owner_ids:
                owner_ids.append(current_user.id)
            # Для админа используем None, чтобы не фильтровать по business_owner_id
            # но проверим доступ после получения
            owner_id = None
        else:
            # У админа нет business_id - может видеть только своих сотрудников
            owner_id = current_user.id
    else:
        owner_id = get_business_owner_id(current_user)
        if current_user.role != "admin":
            owner_id = current_user.id
    
    employee = crud_employee.get_employee(
        db=db,
        employee_id=employee_id,
        business_owner_id=owner_id
    )
    
    if not employee:
        raise HTTPException(status_code=404, detail=_("employee_not_found", locale))
    
    # Проверяем доступ для админа (если у админа есть business_id)
    if current_user.role == "admin" and admin_business_id and len(owner_ids) > 0:
        # Проверяем, что сотрудник принадлежит бизнесу админа
        employee_user = employee.employee_user
        if employee_user and hasattr(employee_user, 'business_id'):
            if employee_user.business_id != admin_business_id:
                # Также проверяем, что business_owner_id входит в список владельцев бизнеса
                if employee.business_owner_id not in owner_ids:
                    raise HTTPException(status_code=404, detail=_("employee_not_found", locale))
    
    # Преобразуем в формат EmployeeOut
    emp_dict = {
        "id": employee.id,
        "employee_user_id": employee.employee_user_id,
        "business_owner_id": employee.business_owner_id,
        "position": employee.position,
        "permissions": employee.permissions,
        "commission_percentage": employee.commission_percentage or 0.0,
        "base_salary": employee.base_salary or 0.0,
        "is_active": employee.is_active,
        "notes": employee.notes,
        "hired_at": employee.hired_at,
        "fired_at": employee.fired_at,
        "employee_user": None
    }
    
    if employee.employee_user:
        emp_dict["employee_user"] = {
            "id": employee.employee_user.id,
            "full_name": employee.employee_user.full_name,
            "email": employee.employee_user.email,
            "phone": employee.employee_user.phone,
            "role": employee.employee_user.role,
            "specialist_type": getattr(employee.employee_user, 'specialist_type', None),
            "business_id": getattr(employee.employee_user, 'business_id', None),
        }
    
    return EmployeeOut(**emp_dict)


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
    
    owner_id = None
    if current_user.role == "admin":
        admin_business_id = current_user.business_id if hasattr(current_user, 'business_id') else None
        if not admin_business_id:
            owner_id = current_user.id
    else:
        owner_id = get_business_owner_id(current_user)
        if current_user.role != "admin":
            owner_id = current_user.id
    
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
    
    # Преобразуем в формат EmployeeOut
    emp_dict = {
        "id": updated_employee.id,
        "employee_user_id": updated_employee.employee_user_id,
        "business_owner_id": updated_employee.business_owner_id,
        "position": updated_employee.position,
        "permissions": updated_employee.permissions,
        "commission_percentage": updated_employee.commission_percentage or 0.0,
        "base_salary": updated_employee.base_salary or 0.0,
        "is_active": updated_employee.is_active,
        "notes": updated_employee.notes,
        "hired_at": updated_employee.hired_at,
        "fired_at": updated_employee.fired_at,
        "employee_user": None
    }
    
    if updated_employee.employee_user:
        emp_dict["employee_user"] = {
            "id": updated_employee.employee_user.id,
            "full_name": updated_employee.employee_user.full_name,
            "email": updated_employee.employee_user.email,
            "phone": updated_employee.employee_user.phone,
            "role": updated_employee.employee_user.role,
            "specialist_type": getattr(updated_employee.employee_user, 'specialist_type', None),
            "business_id": getattr(updated_employee.employee_user, 'business_id', None),
        }
    
    return EmployeeOut(**emp_dict)


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
    
    owner_id = None
    if current_user.role == "admin":
        # Админ может увольнять сотрудников своего бизнеса
        admin_business_id = current_user.business_id if hasattr(current_user, 'business_id') else None
        if admin_business_id:
            # Находим всех business_owners с таким же business_id
            from app.db.models.user import User
            business_owners = db.query(User.id).filter(
                User.role == "business_owner",
                User.business_id == admin_business_id
            ).all()
            owner_ids = [bo[0] for bo in business_owners]
            if current_user.id not in owner_ids:
                owner_ids.append(current_user.id)
            # Для админа используем None, чтобы не фильтровать по business_owner_id
            owner_id = None
        else:
            # У админа нет business_id - может увольнять только своих сотрудников
            owner_id = current_user.id
    else:
        owner_id = get_business_owner_id(current_user)
        if current_user.role != "admin":
            owner_id = current_user.id
    
    employee = crud_employee.get_employee(
        db=db,
        employee_id=employee_id,
        business_owner_id=owner_id
    )
    
    if not employee:
        raise HTTPException(status_code=404, detail=_("employee_not_found", locale))
    
    # Проверяем доступ для админа (если у админа есть business_id)
    if current_user.role == "admin" and admin_business_id:
        employee_user = employee.employee_user
        if employee_user and hasattr(employee_user, 'business_id'):
            if employee_user.business_id != admin_business_id:
                if employee.business_owner_id not in owner_ids:
                    raise HTTPException(status_code=404, detail=_("employee_not_found", locale))
    
    if not employee.is_active:
        raise HTTPException(status_code=400, detail=_("employee_already_fired", locale))
    
    fired_employee = crud_employee.fire_employee(db=db, employee=employee)
    
    # Преобразуем в формат EmployeeOut
    emp_dict = {
        "id": fired_employee.id,
        "employee_user_id": fired_employee.employee_user_id,
        "business_owner_id": fired_employee.business_owner_id,
        "position": fired_employee.position,
        "permissions": fired_employee.permissions,
        "commission_percentage": fired_employee.commission_percentage or 0.0,
        "base_salary": fired_employee.base_salary or 0.0,
        "is_active": fired_employee.is_active,
        "notes": fired_employee.notes,
        "hired_at": fired_employee.hired_at,
        "fired_at": fired_employee.fired_at,
        "employee_user": None
    }
    
    if fired_employee.employee_user:
        emp_dict["employee_user"] = {
            "id": fired_employee.employee_user.id,
            "full_name": fired_employee.employee_user.full_name,
            "email": fired_employee.employee_user.email,
            "phone": fired_employee.employee_user.phone,
            "role": fired_employee.employee_user.role,
            "specialist_type": getattr(fired_employee.employee_user, 'specialist_type', None),
            "business_id": getattr(fired_employee.employee_user, 'business_id', None),
        }
    
    return EmployeeOut(**emp_dict)


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
        admin_business_id = current_user.business_id if hasattr(current_user, 'business_id') else None
        if not admin_business_id:
            owner_id = current_user.id
        else:
            owner_id = None  # Админ может видеть сотрудников своего бизнеса
    
    employee = crud_employee.get_employee_by_user_id(
        db=db,
        user_id=user_id,
        business_owner_id=owner_id
    )
    
    if not employee:
        raise HTTPException(status_code=404, detail=_("employee_not_found", locale))
    
    # Преобразуем в формат EmployeeOut
    emp_dict = {
        "id": employee.id,
        "employee_user_id": employee.employee_user_id,
        "business_owner_id": employee.business_owner_id,
        "position": employee.position,
        "permissions": employee.permissions,
        "commission_percentage": employee.commission_percentage or 0.0,
        "base_salary": employee.base_salary or 0.0,
        "is_active": employee.is_active,
        "notes": employee.notes,
        "hired_at": employee.hired_at,
        "fired_at": employee.fired_at,
        "employee_user": None
    }
    
    if employee.employee_user:
        emp_dict["employee_user"] = {
            "id": employee.employee_user.id,
            "full_name": employee.employee_user.full_name,
            "email": employee.employee_user.email,
            "phone": employee.employee_user.phone,
            "role": employee.employee_user.role,
            "specialist_type": getattr(employee.employee_user, 'specialist_type', None),
            "business_id": getattr(employee.employee_user, 'business_id', None),
        }
    
    return EmployeeOut(**emp_dict)


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

