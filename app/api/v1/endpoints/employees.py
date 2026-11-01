from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.api import deps
from app.api.private_routes import NoStoreRoute
from app.api.core.i18n import _
from app.api.core.logger import get_logger
from app.api.core.pagination import MAX_PAGE, PaginationParams
from app.api.crud import crud_employee, crud_salary
from app.api.core.payroll_money import PayrollError
from app.db.models.user import User
from app.db.models.employee import Employee
from app.schemas.employee import EmployeeCreate, EmployeeUpdate, EmployeeOut, EmployeeCreateWithUser, EmployeeCreateResponse
from app.schemas.salary import (
    SalaryCalculationOut, 
    SalaryCalculationUpdate,
    CalculateSalaryRequest,
    SalaryCalculationDetail,
    SalaryAdjustmentCreate,
    SalaryAdjustmentOut,
    SalaryPaymentCreate,
    SalaryPaymentUpdate,
    SalaryPaymentOut
)
from app.api.core.clock import utcnow
router = APIRouter(route_class=NoStoreRoute)
logger = get_logger("employees")
def _employee_payroll_error(db, exc, locale):
    db.rollback()
    local = {
        'en': {'payroll_invalid_amount': 'Enter a finite amount with at most two decimal places.', 'payroll_invalid_rate': 'Commission must be between 0 and 100.', 'payroll_currency_review': 'Choose a supported ISO 4217 currency.', 'payroll_currency_required': 'Choose a payroll currency.'},
        'ru': {'payroll_invalid_amount': 'Укажите конечную сумму с точностью до копейки.', 'payroll_invalid_rate': 'Процент комиссии должен быть от 0 до 100.', 'payroll_currency_review': 'Укажите поддерживаемую валюту ISO 4217.', 'payroll_currency_required': 'Выберите валюту расчёта зарплаты.'},
        'cz': {'payroll_invalid_amount': 'Zadejte konečnou částku s nejvýše dvěma desetinnými místy.', 'payroll_invalid_rate': 'Provize musí být od 0 do 100.', 'payroll_currency_review': 'Vyberte podporovanou měnu ISO 4217.', 'payroll_currency_required': 'Vyberte měnu výpočtu mzdy.'},
    }
    messages = {**globals().get('_SALARY_MESSAGES', {}).get(locale, {}), **local.get(locale, local['en'])}
    return HTTPException(status_code=400, detail={'reason': exc.reason, 'message': messages.get(exc.reason, _(exc.reason, locale)), **exc.context})

def can_manage_employees(current_user: User) -> bool:
    return current_user.role in ["admin", "business_owner"]
def get_business_owner_id(current_user: User) -> Optional[int]:
    if current_user.role == "business_owner":
        return current_user.id
    return None
def _manager_owner_id(current_user: User, locale: str) -> Optional[int]:
    # owners/admins manage staff; a platform admin isn't tied to one owner
    from app.api.core.permissions import can_manage_employees
    if not can_manage_employees(current_user):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    return None if current_user.role == "admin" else get_business_owner_id(current_user)
def _employee_for_viewer(db: Session, current_user: User, employee_id: int, locale: str):
    """The employee record, for the employee themself or for whoever manages them."""
    # staff made via create-with-user have role "specialist", not "employee" - so look at the
    # employee record itself instead of the role
    own = crud_employee.get_employee_by_user_id(db, current_user.id)
    if own and own.id == employee_id:
        return own
    owner_id = _manager_owner_id(current_user, locale)
    employee = crud_employee.get_employee(db=db, employee_id=employee_id, business_owner_id=owner_id)
    if not employee:
        raise HTTPException(status_code=404, detail=_("employee_not_found", locale))
    return employee
@router.post("/", response_model=EmployeeOut, summary="Hire employee")
def create_employee(
    employee: EmployeeCreate,
    business_owner_id: Optional[int] = Query(None, description="Business owner ID (optional, for admin)"),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    if not can_manage_employees(current_user):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    if current_user.role == "admin":
        owner_id = employee.business_owner_id or business_owner_id
        if not owner_id:
            if current_user.business_id:
                business_owner = db.query(User).filter(
                    User.business_id == current_user.business_id,
                    User.role == "business_owner"
                ).first()
                if business_owner:
                    owner_id = business_owner.id
            if not owner_id:
                owner_id = current_user.id
    elif current_user.role == "business_owner":
        owner_id = current_user.id
    else:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    # hiring by id is only for people already in the owner's business. Anyone else
    # (a solo specialist, someone from another business) can't be pulled in without asking -
    # new staff come through create-with-user
    owner = db.query(User).filter(User.id == owner_id).first()
    target = db.query(User).filter(User.id == employee.employee_user_id).first()
    if not owner or not owner.business_id or not target or target.business_id != owner.business_id:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    try:
        new_employee = crud_employee.create_employee(
            db=db,
            employee_in=employee,
            business_owner_id=owner_id
        )
        from sqlalchemy.orm import joinedload
        employee_with_user = db.query(Employee).options(
            joinedload(Employee.employee_user)
        ).filter(Employee.id == new_employee.id).first()
        if not employee_with_user:
            raise HTTPException(status_code=500, detail="Failed to load created employee")
        emp_dict = {
            "id": employee_with_user.id,
            "employee_user_id": employee_with_user.employee_user_id,
            "business_owner_id": employee_with_user.business_owner_id,
            "position": employee_with_user.position,
            "permissions": employee_with_user.permissions,
            "commission_percentage": employee_with_user.commission_percentage or 0.0,
            "base_salary": employee_with_user.base_salary or 0.0,
            "base_salary_currency": employee_with_user.base_salary_currency,
            "base_salary_exact": employee_with_user.base_salary_exact,
            "is_active": employee_with_user.is_active,
            "notes": employee_with_user.notes,
            "hired_at": employee_with_user.hired_at,
            "fired_at": employee_with_user.fired_at,
            "employee_user": None
        }
        if employee_with_user.employee_user:
            emp_dict["employee_user"] = {
                "id": employee_with_user.employee_user.id,
                "full_name": employee_with_user.employee_user.full_name,
                "email": employee_with_user.employee_user.email,
                "phone": employee_with_user.employee_user.phone,
                "role": employee_with_user.employee_user.role,
                "specialist_type": getattr(employee_with_user.employee_user, 'specialist_type', None),
                "business_id": getattr(employee_with_user.employee_user, 'business_id', None),
            }
        return EmployeeOut(**emp_dict)
    except PayrollError as exc:
        raise _employee_payroll_error(db, exc, locale)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
@router.post("/create-with-user", response_model=EmployeeCreateResponse, summary="Create employee with new user")
def create_employee_with_user(
    employee_data: EmployeeCreateWithUser,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    if not can_manage_employees(current_user):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    if not employee_data.email and not employee_data.phone:
        raise HTTPException(
            status_code=400,
            detail=_("email_or_phone_required", locale)
        )
    from app.api.crud import crud_user
    existing_user = crud_user.get_by_phone_or_email(db, phone=employee_data.phone, email=employee_data.email)
    if existing_user:
        raise HTTPException(
            status_code=400,
            detail=_("user_already_exists", locale) or f"User with this email/phone already exists (ID: {existing_user.id})"
        )
    business_owner_id = None
    business_id = employee_data.business_id
    if current_user.role == "admin":
        if current_user.business_id:
            business_id = current_user.business_id
            from app.db.models.user import User
            business_owner = db.query(User).filter(
                User.business_id == current_user.business_id,
                User.role == "business_owner"
            ).first()
            if business_owner:
                business_owner_id = business_owner.id
            else:
                business_owner_id = current_user.id
        else:
            business_owner_id = current_user.id
            business_id = None  
    elif current_user.role == "business_owner":
        business_owner_id = current_user.id
        business_id = current_user.business_id if hasattr(current_user, 'business_id') else None
    if not business_owner_id:
        raise HTTPException(
            status_code=400,
            detail=_("business_owner_id_required", locale)
        )
    try:
        validated_salary_terms = crud_employee.salary_terms(db, employee_data.model_dump(include={'base_salary', 'base_salary_currency', 'commission_percentage'}, exclude_unset=True), business_owner_id=business_owner_id)
    except PayrollError as exc:
        raise _employee_payroll_error(db, exc, locale)
    from app.api.core.security import generate_secure_password
    generated_password = generate_secure_password(12)
    phone_for_validation = employee_data.phone
    if phone_for_validation:
        from app.api.core.security import validate_phone
        is_valid, error_msg = validate_phone(phone_for_validation)
        if not is_valid:
            if employee_data.email:
                phone_for_validation = None
            else:
                raise HTTPException(status_code=400, detail=_("invalid_phone_or_email", locale))
    if not phone_for_validation and not employee_data.email:
        raise HTTPException(status_code=400, detail=_("email_or_phone_required", locale))
    from app.schemas.user import UserCreate
    from pydantic import ValidationError
    try:
        user_create = UserCreate(
            full_name=employee_data.full_name,
            phone=phone_for_validation,
            email=employee_data.email,
            password=generated_password,
            role="specialist",  
            specialist_type=employee_data.specialist_type,
            business_id=business_id,  
        )
    except ValidationError as e:
        logger.info(f"create-with-user rejected: {[(err['loc'], err['type']) for err in e.errors()]}")
        raise HTTPException(status_code=400, detail=_("invalid_user_data", locale))
    try:
        new_user = crud_user.create_user(db, user_create, moderation_status="approved", with_trial=False)
    except Exception as e:
        error_msg = str(e)
        if "already exists" in error_msg.lower() or "unique" in error_msg.lower():
            raise HTTPException(status_code=400, detail=_("user_already_exists", locale))
        logger.warning(f"create-with-user failed: {error_msg}")
        raise HTTPException(status_code=400, detail=_("user_create_failed", locale))
    from app.schemas.employee import EmployeeCreate
    employee_create = EmployeeCreate(
        employee_user_id=new_user.id,
        position=employee_data.position,
        permissions=employee_data.permissions,
        commission_percentage=employee_data.commission_percentage,
        base_salary=employee_data.base_salary,
        base_salary_currency=validated_salary_terms["base_salary_currency"],
        notes=employee_data.notes,
        business_owner_id=business_owner_id,
    )
    logger.info(f"Creating employee: user_id={new_user.id}, business_owner_id={business_owner_id}, "
                f"admin_id={current_user.id}, business_id={business_id}")
    new_employee = crud_employee.create_employee(db, employee_create, business_owner_id=business_owner_id)
    logger.info(f"Employee created: employee_id={new_employee.id}, employee_user_id={new_employee.employee_user_id}, "
                f"business_owner_id={new_employee.business_owner_id}, is_active={new_employee.is_active}")
    from app.api.crud import crud_specialist_profile
    from app.schemas.specialist_profile import SpecialistProfileCreate
    try:
        existing_profile = crud_specialist_profile.get_specialist_profile_by_user_id(db, new_user.id)
        if not existing_profile:
            specialist_category = None
            if employee_data.specialist_type:
                from app.db.models.specialist_type import SpecialistType, get_category_for_type
                try:
                    specialist_type_enum = SpecialistType(employee_data.specialist_type)
                    specialist_category = get_category_for_type(specialist_type_enum).value
                except (ValueError, AttributeError):
                    pass
            profile_create = SpecialistProfileCreate(
                user_id=new_user.id,
                specialist_type=employee_data.specialist_type,
                specialist_category=specialist_category,
            )
            new_profile = crud_specialist_profile.create_specialist_profile(db, profile_create)
            new_profile.is_verified = "verified"
            new_profile.verified_at = utcnow()
            db.add(new_profile)
            db.commit()
            logger.info(f"Auto-verified specialist profile for user {new_user.id} (created by admin)")
    except Exception as e:
        logger.warning(f"Failed to auto-create specialist profile for user {new_user.id}: {str(e)}")
    response_data = {
        "id": new_employee.id,
        "business_owner_id": new_employee.business_owner_id,
        "employee_user_id": new_employee.employee_user_id,
        "position": new_employee.position,
        "permissions": new_employee.permissions,
        "commission_percentage": new_employee.commission_percentage or 0.0,
        "base_salary": new_employee.base_salary or 0.0,
        "base_salary_currency": new_employee.base_salary_currency,
        "base_salary_exact": new_employee.base_salary_exact,
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
    page: int = Query(1, ge=1, le=MAX_PAGE),
    per_page: int = Query(20, ge=1, le=100),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    if not can_manage_employees(current_user):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    business_owner_ids = None  
    if current_user.role == "admin":
        admin_business_id = current_user.business_id if hasattr(current_user, 'business_id') else None
        owner_ids = []
        if admin_business_id:
            from app.db.models.user import User
            business_owners = db.query(User.id).filter(
                User.role == "business_owner",
                User.business_id == admin_business_id
            ).all()
            owner_ids = [bo[0] for bo in business_owners]
            if current_user.id not in owner_ids:
                owner_ids.append(current_user.id)
            if len(owner_ids) > 0:
                business_owner_ids = owner_ids
            else:
                business_owner_ids = [current_user.id]
        else:
            business_owner_ids = [current_user.id]
        owner_id = None  
    else:
        owner_id = get_business_owner_id(current_user)
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
    logger.info(f"List employees: current_user_id={current_user.id}, role={current_user.role}, "
                f"business_id={getattr(current_user, 'business_id', None)}, "
                f"business_owner_ids={business_owner_ids}, owner_id={owner_id}, "
                f"is_active={is_active}, found {len(employees)} employees (total: {total})")
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
            "base_salary_currency": emp.base_salary_currency,
            "base_salary_exact": emp.base_salary_exact,
            "is_active": emp.is_active,
            "notes": emp.notes,
            "hired_at": emp.hired_at,
            "fired_at": emp.fired_at,
            "employee_user": None
        }
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
@router.get("/me/salary/current", response_model=SalaryCalculationDetail, summary="Get current unpaid salary")
def get_my_current_salary(
    currency: Optional[str] = Query(None),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    if current_user.role not in ("employee", "specialist"):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    try:
        salary = crud_salary.get_current_unpaid_salary(db=db, employee_user_id=current_user.id, currency=currency)
    except PayrollError as exc:
        raise _employee_payroll_error(db, exc, locale)
    if not salary:
        raise HTTPException(status_code=404, detail=_("no_unpaid_salary", locale))
    employee = crud_employee.get_employee_by_user_id(db, current_user.id)
    adjustments = crud_salary.list_salary_adjustments(db, salary.id)
    payments = crud_salary.list_salary_payments(db, salary.id)
    salary_dict = {
        **salary.__dict__,
        'salary_period': salary.salary_period,
        'adjustments': adjustments,
        'payments': payments,
        'commission_percentage': (salary.salary_period.commission_percentage if salary.salary_period is not None else (employee.commission_percentage or 0.0 if employee else 0.0))
    }
    return SalaryCalculationDetail(**salary_dict)
@router.get("/me/salary/history", response_model=List[SalaryCalculationOut], summary="Get my salary history")
def get_my_salary_history(
    payment_status: Optional[str] = Query(None, description="Filter by payment status: unpaid, partial, paid"),
    limit: int = Query(10, ge=1, le=100, description="Maximum number of records to return"),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    if current_user.role not in ("employee", "specialist"):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    employee = crud_employee.get_employee_by_user_id(db, current_user.id)
    if not employee:
        raise HTTPException(status_code=404, detail=_("employee_not_found", locale))
    pagination = PaginationParams(page=1, per_page=limit)
    salaries, _total = crud_salary.list_salary_calculations(
        db=db,
        employee_id=employee.id,
        payment_status=payment_status,
        pagination=pagination
    )
    return salaries
@router.get("/{employee_id}", response_model=EmployeeOut, summary="Get employee")
def get_employee(
    employee_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    if not can_manage_employees(current_user):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    owner_id = None
    admin_business_id = None
    owner_ids = []
    if current_user.role == "admin":
        admin_business_id = current_user.business_id if hasattr(current_user, 'business_id') else None
        if admin_business_id:
            from app.db.models.user import User
            business_owners = db.query(User.id).filter(
                User.role == "business_owner",
                User.business_id == admin_business_id
            ).all()
            owner_ids = [bo[0] for bo in business_owners]
            if current_user.id not in owner_ids:
                owner_ids.append(current_user.id)
            owner_id = None
        else:
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
    if current_user.role == "admin" and admin_business_id and len(owner_ids) > 0:
        employee_user = employee.employee_user
        if employee_user and hasattr(employee_user, 'business_id'):
            if employee_user.business_id != admin_business_id:
                if employee.business_owner_id not in owner_ids:
                    raise HTTPException(status_code=404, detail=_("employee_not_found", locale))
    emp_dict = {
        "id": employee.id,
        "employee_user_id": employee.employee_user_id,
        "business_owner_id": employee.business_owner_id,
        "position": employee.position,
        "permissions": employee.permissions,
        "commission_percentage": employee.commission_percentage or 0.0,
        "base_salary": employee.base_salary or 0.0,
        "base_salary_currency": employee.base_salary_currency,
        "base_salary_exact": employee.base_salary_exact,
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
    try:
        updated_employee = crud_employee.update_employee(
            db=db, employee=employee, employee_update=employee_update)
    except PayrollError as exc:
        raise _employee_payroll_error(db, exc, locale)
    emp_dict = {
        "id": updated_employee.id,
        "employee_user_id": updated_employee.employee_user_id,
        "business_owner_id": updated_employee.business_owner_id,
        "position": updated_employee.position,
        "permissions": updated_employee.permissions,
        "commission_percentage": updated_employee.commission_percentage or 0.0,
        "base_salary": updated_employee.base_salary or 0.0,
        "base_salary_currency": updated_employee.base_salary_currency,
        "base_salary_exact": updated_employee.base_salary_exact,
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
    if not can_manage_employees(current_user):
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    owner_id = None
    if current_user.role == "admin":
        admin_business_id = current_user.business_id if hasattr(current_user, 'business_id') else None
        if admin_business_id:
            from app.db.models.user import User
            business_owners = db.query(User.id).filter(
                User.role == "business_owner",
                User.business_id == admin_business_id
            ).all()
            owner_ids = [bo[0] for bo in business_owners]
            if current_user.id not in owner_ids:
                owner_ids.append(current_user.id)
            owner_id = None
        else:
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
    if current_user.role == "admin" and admin_business_id:
        employee_user = employee.employee_user
        if employee_user and hasattr(employee_user, 'business_id'):
            if employee_user.business_id != admin_business_id:
                if employee.business_owner_id not in owner_ids:
                    raise HTTPException(status_code=404, detail=_("employee_not_found", locale))
    if not employee.is_active:
        raise HTTPException(status_code=400, detail=_("employee_already_fired", locale))
    fired_employee = crud_employee.fire_employee(db=db, employee=employee)
    emp_dict = {
        "id": fired_employee.id,
        "employee_user_id": fired_employee.employee_user_id,
        "business_owner_id": fired_employee.business_owner_id,
        "position": fired_employee.position,
        "permissions": fired_employee.permissions,
        "commission_percentage": fired_employee.commission_percentage or 0.0,
        "base_salary": fired_employee.base_salary or 0.0,
        "base_salary_currency": fired_employee.base_salary_currency,
        "base_salary_exact": fired_employee.base_salary_exact,
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
    if current_user.id != user_id:
        _manager_owner_id(current_user, locale)
    owner_id = None
    if current_user.role == "business_owner":
        owner_id = current_user.id
    elif current_user.role == "admin":
        admin_business_id = current_user.business_id if hasattr(current_user, 'business_id') else None
        if not admin_business_id:
            owner_id = current_user.id
        else:
            owner_id = None  
    employee = crud_employee.get_employee_by_user_id(
        db=db,
        user_id=user_id,
        business_owner_id=owner_id
    )
    if not employee:
        raise HTTPException(status_code=404, detail=_("employee_not_found", locale))
    emp_dict = {
        "id": employee.id,
        "employee_user_id": employee.employee_user_id,
        "business_owner_id": employee.business_owner_id,
        "position": employee.position,
        "permissions": employee.permissions,
        "commission_percentage": employee.commission_percentage or 0.0,
        "base_salary": employee.base_salary or 0.0,
        "base_salary_currency": employee.base_salary_currency,
        "base_salary_exact": employee.base_salary_exact,
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
from functools import wraps as _salary_wraps
from app.api.core.payroll_money import PayrollError as _PayrollError


_SALARY_MESSAGES = {
    'ru': {
        'payroll_invalid_amount': 'Укажите конечную сумму с точностью до копейки.',
        'payroll_invalid_rate': 'Процент комиссии должен быть от 0 до 100.',
        'payroll_currency_review': 'Укажите поддерживаемую валюту ISO 4217.',
        'payroll_employee_missing': 'Сотрудник не найден.',
        'payroll_employee_inactive': 'Расчёт недоступен для неактивного сотрудника.',
        'payroll_invalid_period': 'Начало периода должно быть раньше окончания.',
        'payroll_business_missing': 'Сотрудник должен относиться к бизнесу.',
        'payroll_business_mismatch': 'Бизнес выплаты должен совпадать с расчётом.',
        'payroll_calculation_missing': 'Расчёт зарплаты не найден.',
        'payroll_snapshot_review': 'Условия и суммы расчёта зарплаты требуют проверки.',
        'payroll_currency_required': 'Выберите валюту расчёта зарплаты.',
        'payroll_visit_review': 'Проверьте валюту и суммы завершённых визитов.',
        'payroll_legacy_review': 'Старые денежные данные требуют проверки перед изменением.',
        'payroll_correction_required': 'Завершённые выплаты требуют отдельной финансовой корректировки.',
        'payroll_payment_required': 'Для изменения оплаты создайте выплату через журнал платежей.',
        'payroll_period_overlap': 'Период пересекается с существующим расчётом зарплаты.',
        'payroll_payment_exceeds_remaining': 'Сумма выплаты превышает остаток зарплаты.',
        'payroll_total_below_paid': 'Итог зарплаты не может быть отрицательным или меньше выплаченной суммы.',
        'payroll_currency_mismatch': 'Валюта должна совпадать с валютой расчёта зарплаты.',
        'payroll_transaction_failed': 'Не удалось сохранить зарплату и связанный расход. Изменения отменены.',
    },
    'cz': {
        'payroll_invalid_amount': 'Zadejte konečnou částku s nejvýše dvěma desetinnými místy.',
        'payroll_invalid_rate': 'Provize musí být od 0 do 100.',
        'payroll_currency_review': 'Vyberte podporovanou měnu ISO 4217.',
        'payroll_employee_missing': 'Zaměstnanec nebyl nalezen.',
        'payroll_employee_inactive': 'Pro neaktivního zaměstnance nelze vypočítat mzdu.',
        'payroll_invalid_period': 'Začátek období musí předcházet konci.',
        'payroll_business_missing': 'Zaměstnanec musí patřit k firmě.',
        'payroll_business_mismatch': 'Firma výplaty musí odpovídat výpočtu mzdy.',
        'payroll_calculation_missing': 'Výpočet mzdy nebyl nalezen.',
        'payroll_snapshot_review': 'Zkontrolujte podmínky a částky výpočtu mzdy.',
        'payroll_currency_required': 'Vyberte měnu výpočtu mzdy.',
        'payroll_visit_review': 'Zkontrolujte měnu a částky dokončených návštěv.',
        'payroll_legacy_review': 'Původní finanční údaje vyžadují kontrolu.',
        'payroll_correction_required': 'Dokončená výplata vyžaduje samostatnou finanční opravu.',
        'payroll_payment_required': 'Platbu zadejte do evidence výplat.',
        'payroll_period_overlap': 'Období se překrývá s existujícím výpočtem mzdy.',
        'payroll_payment_exceeds_remaining': 'Výplata převyšuje zbývající mzdu.',
        'payroll_total_below_paid': 'Mzda nesmí být záporná ani nižší než vyplacená částka.',
        'payroll_currency_mismatch': 'Měna musí odpovídat výpočtu mzdy.',
        'payroll_transaction_failed': 'Mzdu a výdaj se nepodařilo uložit. Změny byly vráceny.',
    },
    'en': {
        'payroll_invalid_amount': 'Enter a finite amount with at most two decimal places.',
        'payroll_invalid_rate': 'Commission must be between 0 and 100.',
        'payroll_currency_review': 'Choose a supported ISO 4217 currency.',
        'payroll_employee_missing': 'Employee not found.',
        'payroll_employee_inactive': 'Cannot calculate payroll for an inactive employee.',
        'payroll_invalid_period': 'Period start must be before its end.',
        'payroll_business_missing': 'Employee must belong to a business.',
        'payroll_business_mismatch': 'Payment business must match the payroll calculation.',
        'payroll_calculation_missing': 'Payroll calculation not found.',
        'payroll_snapshot_review': 'Review payroll calculation terms and amounts.',
        'payroll_currency_required': 'Choose a payroll currency.',
        'payroll_visit_review': 'Review currency and amounts of completed visits.',
        'payroll_legacy_review': 'Review legacy financial data before changing it.',
        'payroll_correction_required': 'Completed payments require a separate financial correction.',
        'payroll_payment_required': 'Create a payment through the payment ledger.',
        'payroll_period_overlap': 'The period overlaps an existing payroll calculation.',
        'payroll_payment_exceeds_remaining': 'The payment exceeds the remaining salary.',
        'payroll_total_below_paid': 'Salary cannot be negative or below the paid amount.',
        'payroll_currency_mismatch': 'Currency must match the payroll calculation.',
        'payroll_transaction_failed': 'Payroll and expense could not be saved. Changes were rolled back.',
    },
}


def _salary_operation(fn):
    @_salary_wraps(fn)
    def operation(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except HTTPException:
            raise
        except Exception as exc:
            db = kwargs.get('db')
            if db is not None:
                db.rollback()
            reason = exc.reason if isinstance(exc, _PayrollError) else 'payroll_transaction_failed'
            messages = _SALARY_MESSAGES.get(kwargs.get('locale'), _SALARY_MESSAGES['en'])
            detail = {'reason': reason, 'message': messages.get(reason, messages['payroll_transaction_failed'])}
            if isinstance(exc, _PayrollError):
                detail.update(exc.context)
            raise HTTPException(status_code=400, detail=detail) from None
    return operation


@router.post("/{employee_id}/salary/calculate", response_model=SalaryCalculationOut, summary="Calculate salary for employee")
@_salary_operation
def calculate_employee_salary(
    employee_id: int,
    request: CalculateSalaryRequest,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    owner_id = _manager_owner_id(current_user, locale)
    employee = crud_employee.get_employee(
        db=db,
        employee_id=employee_id,
        business_owner_id=owner_id
    )
    if not employee:
        raise HTTPException(status_code=404, detail=_("employee_not_found", locale))
    if not employee.is_active:
        raise HTTPException(status_code=400, detail=_("Cannot calculate salary for inactive employee", locale))
    if request.period_start >= request.period_end:
        raise HTTPException(status_code=400, detail=_("period_start must be before period_end", locale))
    try:
        salary_calculation = crud_salary.calculate_salary(
            db=db,
            employee_id=employee_id,
            period_start=request.period_start,
            period_end=request.period_end,
            calculated_by_id=current_user.id,
            notes=request.notes,
            currency=request.currency
        )
        return salary_calculation
    except ValueError:
        raise
@router.get("/{employee_id}/salary/current", response_model=SalaryCalculationDetail, summary="Get current unpaid salary for employee")
@_salary_operation
def get_employee_current_salary(
    employee_id: int,
    currency: Optional[str] = Query(None, pattern=r"^[A-Z]{3}$"),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    _employee_for_viewer(db, current_user, employee_id, locale)
    salary = crud_salary.get_current_unpaid_salary(db=db, employee_id=employee_id, currency=currency)
    if not salary:
        raise HTTPException(status_code=404, detail=_("no_unpaid_salary", locale))
    adjustments = crud_salary.list_salary_adjustments(db, salary.id)
    payments = crud_salary.list_salary_payments(db, salary.id)
    salary_dict = {
        **salary.__dict__,
        'salary_period': salary.salary_period,
        'adjustments': adjustments,
        'payments': payments,
        'commission_percentage': salary.salary_period.commission_percentage if salary.salary_period else None
    }
    return SalaryCalculationDetail(**salary_dict)
@router.get("/{employee_id}/salary/history", response_model=List[SalaryCalculationOut], summary="Get salary history for employee")
@_salary_operation
def get_employee_salary_history(
    employee_id: int,
    is_paid: Optional[bool] = Query(None, description="Filter by paid status"),
    page: int = Query(1, ge=1, le=MAX_PAGE),
    per_page: int = Query(20, ge=1, le=100),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    from app.api.core.permissions import can_manage_employees
    if current_user.role == "employee":
        employee = crud_employee.get_employee_by_user_id(db, current_user.id)
        if not employee or employee.id != employee_id:
            raise HTTPException(status_code=403, detail=_("not_allowed", locale))
        owner_id = None
    elif can_manage_employees(current_user):
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
@router.get("/{employee_id}/salary/{salary_id}", response_model=SalaryCalculationDetail, summary="Get salary calculation details")
@_salary_operation
def get_salary_calculation(
    employee_id: int,
    salary_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    employee = _employee_for_viewer(db, current_user, employee_id, locale)
    salary = crud_salary.get_salary_calculation(db=db, salary_id=salary_id, employee_id=employee_id)
    if not salary:
        raise HTTPException(status_code=404, detail=_("salary_calculation_not_found", locale))
    adjustments = crud_salary.list_salary_adjustments(db, salary_id)
    payments = crud_salary.list_salary_payments(db, salary_id)
    from app.schemas.salary import SalaryCalculationDetail
    salary_dict = {
        **salary.__dict__,
        'salary_period': salary.salary_period,
        'adjustments': adjustments,
        'payments': payments,
        'commission_percentage': salary.salary_period.commission_percentage if salary.salary_period else None
    }
    return SalaryCalculationDetail(**salary_dict)
@router.put("/{employee_id}/salary/{salary_id}", response_model=SalaryCalculationOut, summary="Update salary calculation (mark as paid)")
@_salary_operation
def update_salary_calculation(
    employee_id: int,
    salary_id: int,
    update: SalaryCalculationUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    owner_id = _manager_owner_id(current_user, locale)
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
@router.post("/{employee_id}/salary/{salary_id}/adjustments", response_model=SalaryAdjustmentOut, summary="Add salary adjustment (bonus/penalty)")
@_salary_operation
def create_salary_adjustment(
    employee_id: int,
    salary_id: int,
    adjustment: SalaryAdjustmentCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    owner_id = _manager_owner_id(current_user, locale)
    employee = crud_employee.get_employee(db=db, employee_id=employee_id, business_owner_id=owner_id)
    if not employee:
        raise HTTPException(status_code=404, detail=_("employee_not_found", locale))
    salary = crud_salary.get_salary_calculation(db=db, salary_id=salary_id, employee_id=employee_id)
    if not salary:
        raise HTTPException(status_code=404, detail=_("salary_calculation_not_found", locale))
    valid_types = ['bonus', 'penalty', 'overtime', 'allowance', 'deduction']
    if adjustment.type not in valid_types:
        raise HTTPException(status_code=400, detail=_("Invalid adjustment type", locale))
    result = crud_salary.create_salary_adjustment(
        db=db,
        salary_calculation_id=salary_id,
        adjustment_in=adjustment,
        created_by_id=current_user.id
    )
    return result
@router.get("/{employee_id}/salary/{salary_id}/adjustments", response_model=List[SalaryAdjustmentOut], summary="Get salary adjustments")
@_salary_operation
def get_salary_adjustments(
    employee_id: int,
    salary_id: int,
    adjustment_type: Optional[str] = Query(None, description="Filter by type"),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    _employee_for_viewer(db, current_user, employee_id, locale)
    salary = crud_salary.get_salary_calculation(db=db, salary_id=salary_id, employee_id=employee_id)
    if not salary:
        raise HTTPException(status_code=404, detail=_("salary_calculation_not_found", locale))
    adjustments = crud_salary.list_salary_adjustments(db, salary_id, adjustment_type)
    return adjustments
@router.delete("/{employee_id}/salary/{salary_id}/adjustments/{adjustment_id}", summary="Delete salary adjustment")
@_salary_operation
def delete_salary_adjustment(
    employee_id: int,
    salary_id: int,
    adjustment_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    owner_id = _manager_owner_id(current_user, locale)
    employee = crud_employee.get_employee(db=db, employee_id=employee_id, business_owner_id=owner_id)
    if not employee:
        raise HTTPException(status_code=404, detail=_("employee_not_found", locale))
    salary = crud_salary.get_salary_calculation(db=db, salary_id=salary_id, employee_id=employee_id)
    if not salary:
        raise HTTPException(status_code=404, detail=_("salary_calculation_not_found", locale))
    success = crud_salary.delete_salary_adjustment(db=db, adjustment_id=adjustment_id, salary_calculation_id=salary_id)
    if not success:
        raise HTTPException(status_code=404, detail=_("adjustment_not_found", locale))
    return {"message": _("adjustment_deleted", locale)}
@router.post("/{employee_id}/salary/{salary_id}/payments", response_model=SalaryPaymentOut, summary="Create salary payment")
@_salary_operation
def create_salary_payment(
    employee_id: int,
    salary_id: int,
    payment: SalaryPaymentCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    owner_id = _manager_owner_id(current_user, locale)
    employee = crud_employee.get_employee(db=db, employee_id=employee_id, business_owner_id=owner_id)
    if not employee:
        raise HTTPException(status_code=404, detail=_("employee_not_found", locale))
    salary = crud_salary.get_salary_calculation(db=db, salary_id=salary_id, employee_id=employee_id)
    if not salary:
        raise HTTPException(status_code=404, detail=_("salary_calculation_not_found", locale))
    from app.api.core.permissions import get_user_business_id
    business_id = get_user_business_id(current_user, db)
    if not business_id and employee.business_owner_id:
        owner_user = db.query(User).filter(User.id == employee.business_owner_id).first()
        if owner_user:
            business_id = owner_user.business_id if hasattr(owner_user, 'business_id') else None
    result = crud_salary.create_salary_payment(
        db=db,
        salary_calculation_id=salary_id,
        payment_in=payment,
        created_by_id=current_user.id,
        business_id=business_id
    )
    return result
@router.get("/{employee_id}/salary/{salary_id}/payments", response_model=List[SalaryPaymentOut], summary="Get salary payments")
@_salary_operation
def get_salary_payments(
    employee_id: int,
    salary_id: int,
    status: Optional[str] = Query(None, description="Filter by payment status"),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    _employee_for_viewer(db, current_user, employee_id, locale)
    salary = crud_salary.get_salary_calculation(db=db, salary_id=salary_id, employee_id=employee_id)
    if not salary:
        raise HTTPException(status_code=404, detail=_("salary_calculation_not_found", locale))
    payments = crud_salary.list_salary_payments(db, salary_id, status)
    return payments
@router.put("/{employee_id}/salary/{salary_id}/payments/{payment_id}", response_model=SalaryPaymentOut, summary="Update salary payment")
@_salary_operation
def update_salary_payment(
    employee_id: int,
    salary_id: int,
    payment_id: int,
    update: SalaryPaymentUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    owner_id = _manager_owner_id(current_user, locale)
    employee = crud_employee.get_employee(db=db, employee_id=employee_id, business_owner_id=owner_id)
    if not employee:
        raise HTTPException(status_code=404, detail=_("employee_not_found", locale))
    salary = crud_salary.get_salary_calculation(db=db, salary_id=salary_id, employee_id=employee_id)
    if not salary:
        raise HTTPException(status_code=404, detail=_("salary_calculation_not_found", locale))
    payment = crud_salary.get_salary_payment(db=db, payment_id=payment_id, salary_calculation_id=salary_id)
    if not payment:
        raise HTTPException(status_code=404, detail=_("payment_not_found", locale))
    result = crud_salary.update_salary_payment(db=db, payment=payment, update=update)
    return result
@router.delete("/{employee_id}/salary/{salary_id}/payments/{payment_id}", summary="Delete salary payment")
@_salary_operation
def delete_salary_payment(
    employee_id: int,
    salary_id: int,
    payment_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    owner_id = _manager_owner_id(current_user, locale)
    employee = crud_employee.get_employee(db=db, employee_id=employee_id, business_owner_id=owner_id)
    if not employee:
        raise HTTPException(status_code=404, detail=_("employee_not_found", locale))
    salary = crud_salary.get_salary_calculation(db=db, salary_id=salary_id, employee_id=employee_id)
    if not salary:
        raise HTTPException(status_code=404, detail=_("salary_calculation_not_found", locale))
    success = crud_salary.delete_salary_payment(db=db, payment_id=payment_id, salary_calculation_id=salary_id)
    if not success:
        raise HTTPException(status_code=404, detail=_("payment_not_found", locale))
    return {"message": _("payment_deleted", locale)}
