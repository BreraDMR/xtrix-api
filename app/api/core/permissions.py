from typing import Optional, List, Set
from sqlalchemy.orm import Session
from app.db.models.user import User
from app.db.models.client import Client
from app.db.models.visit import Visit
from app.db.models.service import Service
from app.db.models.client_business_relation import ClientBusinessRelation
from app.db.models.client_specialist_relation import ClientSpecialistRelation
from app.schemas.employee import EmployeePermissions
def can_manage_employees(user: User) -> bool:
    return user.role in ["admin", "business_owner"]
def get_staff_record(db: Session, user: User):
    """The active Employee row if this user works for someone, else None.
    Staff made through create-with-user have role "specialist", so the role alone
    can't tell staff from a solo specialist - the Employee row does."""
    if user.role not in ("employee", "specialist"):
        return None
    from app.db.models.employee import Employee
    return db.query(Employee).filter(
        Employee.employee_user_id == user.id,
        Employee.is_active == True,  # noqa: E712
    ).populate_existing().first()
def _staff_permissions(employee) -> EmployeePermissions:
    return EmployeePermissions(**(employee.permissions or {}))
def _allowed(user: User, db: Session, flag: str) -> bool:
    # owners and admins can do everything, staff only what the owner ticked,
    # a solo specialist runs their own little business
    if user.role in ("admin", "business_owner"):
        return True
    staff = get_staff_record(db, user)
    if staff:
        return getattr(_staff_permissions(staff), flag)
    return user.role == "specialist" and user.employer_id is None
def can_view_financials(user: User, db: Session) -> bool:
    return _allowed(user, db, "can_view_financials")
def can_manage_services(user: User, db: Session) -> bool:
    return _allowed(user, db, "can_manage_services")
def can_create_visits(user: User, db: Session) -> bool:
    return _allowed(user, db, "can_create_visits")
def can_edit_clients(user: User, db: Session) -> bool:
    return _allowed(user, db, "can_edit_clients")
def get_business_client_ids(db: Session, business_id: int) -> Optional[Set[int]]:
    if business_id is None:
        return None
    relations = db.query(ClientBusinessRelation.client_id).filter(
        ClientBusinessRelation.business_id == business_id
    ).all()
    return {rel[0] for rel in relations}
def get_specialist_client_ids(db: Session, specialist_id: int, business_id: Optional[int] = None) -> Set[int]:
    client_ids = set()
    relations = db.query(ClientSpecialistRelation.client_id).filter(
        ClientSpecialistRelation.specialist_id == specialist_id
    )
    if business_id:
        relations = relations.filter(ClientSpecialistRelation.business_id == business_id)
    client_ids.update({rel[0] for rel in relations.all()})
    owner_clients = db.query(Client.id).filter(Client.owner_id == specialist_id).all()
    client_ids.update({c[0] for c in owner_clients})
    return client_ids
def get_accessible_client_ids(db: Session, user: User) -> Optional[Set[int]]:
    if user.role == "admin":
        business_id = getattr(user, 'business_id', None)
        if business_id:
            return get_business_client_ids(db, business_id)
        return set()
    if user.role == "business_owner":
        business_id = getattr(user, 'business_id', None)
        if business_id:
            return get_business_client_ids(db, business_id)
        return set()  
    employee = get_staff_record(db, user)
    if employee:
        owner = db.query(User).filter(User.id == employee.business_owner_id).first()
        business_ids = get_business_client_ids(db, owner.business_id) if owner and owner.business_id else set()
        assigned = _staff_permissions(employee).assigned_client_ids
        if assigned:
            # only ever the owner's own clients, whatever ids got saved in the permissions
            return set(assigned) & business_ids
        return business_ids
    if user.role == "employee" or (user.role == "specialist" and user.employer_id is not None):
        # fired, or an employment link that no longer grants access
        return set()
    if user.role == "specialist":
        business_id = getattr(user, 'business_id', None)
        return get_specialist_client_ids(db, user.id, business_id)
    return set()  
def check_client_access(db: Session, user: User, client_id: int) -> bool:
    accessible_ids = get_accessible_client_ids(db, user)
    if accessible_ids is None:  
        return False
    return client_id in accessible_ids
def check_visit_access(db: Session, user: User, visit: Visit) -> bool:
    if user.role == "admin":
        admin_business_id = getattr(user, 'business_id', None)
        if admin_business_id and hasattr(visit, 'business_id'):
            return visit.business_id == admin_business_id
        return False
    if user.role == "business_owner":
        business_id = getattr(user, 'business_id', None)
        if business_id and hasattr(visit, 'business_id'):
            return visit.business_id == business_id
        # no business yet: only their own visits, not everybody's
        return visit.specialist_id == user.id
    if get_staff_record(db, user):
        business_id = get_user_business_id(user, db)
        if not business_id or visit.business_id != business_id:
            return False
        if visit.specialist_id == user.id:
            return True
        return check_client_access(db, user, visit.client_id)
    if user.role == "specialist" and user.employer_id is None:
        return visit.specialist_id == user.id and visit.business_id == user.business_id
    return False
def check_service_access(db: Session, user: User, service: Service) -> bool:
    if user.role == "admin":
        return True
    if user.role == "business_owner":
        business_id = getattr(user, 'business_id', None)
        if business_id and hasattr(service, 'business_id'):
            return service.business_id == business_id
        return service.specialist_id == user.id
    staff = get_staff_record(db, user)
    if staff:
        business_id = get_user_business_id(user, db)
        return bool(business_id) and service.business_id == business_id
    if user.role == "specialist" and user.employer_id is None:
        return service.specialist_id == user.id and service.business_id == user.business_id
    return False
def get_filtered_specialist_id(user: User) -> Optional[int]:
    if user.role == "admin":
        return None  
    if user.role == "business_owner":
        return None  
    return user.id
def get_business_owner_ids_for_filter(current_user: User, db: Session) -> Optional[List[int]]:
    if current_user.role == "admin":
        admin_business_id = current_user.business_id if hasattr(current_user, 'business_id') else None
        if admin_business_id:
            business_owners = db.query(User.id).filter(
                User.role == "business_owner",
                User.business_id == admin_business_id
            ).all()
            return [bo[0] for bo in business_owners]
        return []  
    if current_user.role == "business_owner":
        return [current_user.id]
    return None
def check_specialist_in_scope(db: Session, user: User, specialist_id: int) -> bool:
    # who may be put down as the specialist of a visit/service/schedule on user's behalf
    if user.role == "employee" or (user.role == "specialist" and user.employer_id is not None):
        if not get_staff_record(db, user):
            return False
    if specialist_id == user.id or user.role == "admin":
        return True
    target = db.query(User).filter(User.id == specialist_id, User.deleted_at.is_(None)).first()
    if not target:
        return False
    if user.business_id and target.business_id == user.business_id:
        return True
    from app.db.models.employee import Employee
    owner_id = user.id
    staff = get_staff_record(db, user)
    if staff:
        owner_id = staff.business_owner_id
    elif user.role == "employee":
        owner_id = None
    return bool(owner_id) and db.query(Employee).filter(
        Employee.business_owner_id == owner_id,
        Employee.employee_user_id == specialist_id,
        Employee.is_active == True,  # noqa: E712
    ).first() is not None
def get_user_business_id(current_user: User, db: Session) -> Optional[int]:
    """The business the user works in: their own, or the employer's for an employee."""
    staff = get_staff_record(db, current_user)
    if staff:
        owner = db.query(User).filter(
            User.id == staff.business_owner_id, User.deleted_at.is_(None)
        ).populate_existing().first()
        return owner.business_id if owner else None
    if current_user.role == "employee" or (
        current_user.role == "specialist" and current_user.employer_id is not None
    ):
        return None
    return current_user.business_id
def require_business_id(current_user: User, db: Session, locale: str) -> int:
    # stock, expenses and loyalty only make sense inside a business
    business_id = get_user_business_id(current_user, db)
    if not business_id:
        from fastapi import HTTPException
        from app.api.core.i18n import _
        raise HTTPException(status_code=403, detail=_("business_required", locale))
    return business_id
