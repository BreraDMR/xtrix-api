from typing import Optional, List, Tuple
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, select
from app.api.core import payroll_money
from app.db.models.business import Business
from app.api.core.pagination import PaginationParams
from app.db.models.employee import Employee
from app.db.models.user import User
from app.schemas.employee import EmployeeCreate, EmployeeUpdate, EmployeePermissions
from app.api.core.clock import utcnow
def salary_terms(db, data, business_owner_id=None, employee=None):
    """Validate fresh terms without assigning a unit to legacy amounts."""
    values = dict(data)
    # the web form sends null for an empty field: nothing on create, "leave it" on update
    for key in ("commission_percentage", "base_salary"):
        if key in values and values[key] is None:
            if employee is None:
                values[key] = 0
            else:
                values.pop(key)
    if "commission_percentage" in values:
        values["commission_percentage"] = float(payroll_money.rate(values["commission_percentage"]))
    has_amount = "base_salary" in values
    has_currency = "base_salary_currency" in values
    if employee is not None and not has_amount and not has_currency:
        return values
    if employee is not None and has_currency and not has_amount:
        raise payroll_money.PayrollError('payroll_invalid_amount')
    exact = payroll_money.amount(values.get("base_salary", 0))
    if has_currency and values["base_salary_currency"] is not None:
        unit = payroll_money.currency(values["base_salary_currency"])
    elif employee is not None:
        unit = employee.base_salary_currency
        if unit is not None:
            unit = payroll_money.currency(unit)
        if has_currency and unit is not None:
            raise payroll_money.PayrollError('payroll_currency_review')
    else:
        with db.no_autoflush:
            business_id = db.execute(select(User.business_id).where(User.id == business_owner_id)).scalar_one_or_none()
            unit = db.execute(select(Business.currency).where(Business.id == business_id).with_for_update(read=True)).scalar_one_or_none() if business_id else None
        if unit is not None:
            unit = payroll_money.currency(unit)
    if employee is None and unit is None and exact != 0:
        raise payroll_money.PayrollError('payroll_currency_review')
    values["base_salary"] = float(exact)
    values["base_salary_currency"] = unit
    values["base_salary_exact"] = exact if unit is not None else None
    return values


def create_employee(
    db: Session,
    employee_in: EmployeeCreate,
    business_owner_id: int
) -> Employee:
    employee_user = db.query(User).filter(User.id == employee_in.employee_user_id).first()
    if not employee_user:
        raise ValueError("Employee user not found")
    if employee_user.role not in ["employee", "specialist"]:
        raise ValueError(f"User must have role 'employee' or 'specialist', got '{employee_user.role}'")
    existing_employee = db.query(Employee).filter(
        and_(
            Employee.employee_user_id == employee_in.employee_user_id,
            Employee.is_active == True
        )
    ).first()
    if existing_employee:
        raise ValueError(f"User is already employed by business owner {existing_employee.business_owner_id}")
    permissions = employee_in.permissions
    if permissions is None:
        default_permissions = EmployeePermissions()
        permissions = default_permissions.model_dump(exclude_none=True)
    employee_data = employee_in.model_dump(exclude={"permissions"})
    employee_data = salary_terms(db, employee_data, business_owner_id=business_owner_id)
    employee_data["business_owner_id"] = business_owner_id
    employee_data["permissions"] = permissions
    employee = Employee(**employee_data)
    employee_user.employer_id = business_owner_id
    employee_user.hire_date = utcnow()
    if not employee_user.business_id:
        business_owner = db.query(User).filter(User.id == business_owner_id).first()
        if business_owner and hasattr(business_owner, 'business_id') and business_owner.business_id:
            employee_user.business_id = business_owner.business_id
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
    query = db.query(Employee).filter(Employee.id == employee_id)
    if business_owner_id is not None:
        query = query.filter(Employee.business_owner_id == business_owner_id)
    return query.options(
        joinedload(Employee.employee_user),
        joinedload(Employee.business_owner)
    ).first()
def list_employees(
    db: Session,
    business_owner_id: Optional[int] = None,
    business_owner_ids: Optional[List[int]] = None,  
    is_active: Optional[bool] = None,
    pagination: Optional[PaginationParams] = None
) -> Tuple[List[Employee], int]:
    query = db.query(Employee).options(
        joinedload(Employee.employee_user),
        joinedload(Employee.business_owner)
    )
    if business_owner_id is not None:
        query = query.filter(Employee.business_owner_id == business_owner_id)
    elif business_owner_ids is not None and len(business_owner_ids) > 0:
        query = query.filter(Employee.business_owner_id.in_(business_owner_ids))
    if is_active is not None:
        query = query.filter(Employee.is_active == is_active)
    else:
        query = query.filter(Employee.is_active == True)
    total = query.count()
    query = query.order_by(Employee.id.asc())
    if pagination:
        query = query.offset(pagination.offset).limit(pagination.limit)
    items = query.all()
    return items, total
def update_employee(
    db: Session,
    employee: Employee,
    employee_update: EmployeeUpdate
) -> Employee:
    with db.no_autoflush:
        employee = db.query(Employee).filter(Employee.id == employee.id).populate_existing().with_for_update().one()
    update_data = salary_terms(db, employee_update.model_dump(exclude_unset=True), employee=employee)
    if "permissions" in update_data and update_data["permissions"] is None:
        default_permissions = EmployeePermissions()
        update_data["permissions"] = default_permissions.model_dump()
    for field, value in update_data.items():
        setattr(employee, field, value)
    employee.updated_at = utcnow()
    db.add(employee)
    db.commit()
    db.refresh(employee)
    return employee
def fire_employee(
    db: Session,
    employee: Employee
) -> Employee:
    employee.is_active = False
    employee.fired_at = utcnow()
    employee.updated_at = utcnow()
    employee_user = employee.employee_user
    if employee_user:
        employee_user.employer_id = None
        # they got the business through the job - out of the job, out of the business
        owner = employee.business_owner
        if owner and owner.business_id and employee_user.business_id == owner.business_id:
            employee_user.business_id = None
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
    query = db.query(Employee).filter(Employee.business_owner_id == business_owner_id)
    if active_only:
        query = query.filter(Employee.is_active == True)
    return query.options(
        joinedload(Employee.employee_user)
    ).all()
