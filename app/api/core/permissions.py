from typing import Optional, List, Set
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_
from app.db.models.user import User
from app.db.models.client import Client
from app.db.models.visit import Visit
from app.db.models.service import Service
from app.db.models.client_business_relation import ClientBusinessRelation
from app.db.models.client_specialist_relation import ClientSpecialistRelation
from app.api.crud import crud_employee
from app.schemas.employee import EmployeePermissions
def can_manage_employees(user: User) -> bool:
    return user.role in ["admin", "business_owner"]
def can_view_financials(user: User, db: Session) -> bool:
    if user.role == "admin":
        return True
    if user.role == "business_owner":
        return True
    if user.role == "employee":
        employee = crud_employee.get_employee_by_user_id(db, user.id)
        if employee and employee.permissions:
            permissions = EmployeePermissions(**employee.permissions)
            return permissions.can_view_financials
    return True
def can_manage_services(user: User, db: Session) -> bool:
    if user.role in ["admin", "business_owner", "specialist"]:
        return True
    if user.role == "employee":
        employee = crud_employee.get_employee_by_user_id(db, user.id)
        if employee and employee.permissions:
            permissions = EmployeePermissions(**employee.permissions)
            return permissions.can_manage_services
    return False
def can_create_visits(user: User, db: Session) -> bool:
    if user.role in ["admin", "business_owner", "specialist"]:
        return True
    if user.role == "employee":
        employee = crud_employee.get_employee_by_user_id(db, user.id)
        if employee and employee.permissions:
            permissions = EmployeePermissions(**employee.permissions)
            return permissions.can_create_visits
    return False
def can_edit_clients(user: User, db: Session) -> bool:
    if user.role in ["admin", "business_owner", "specialist"]:
        return True
    if user.role == "employee":
        employee = crud_employee.get_employee_by_user_id(db, user.id)
        if employee and employee.permissions:
            permissions = EmployeePermissions(**employee.permissions)
            return permissions.can_edit_clients
    return False
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
    if user.role == "employee":
        employee = crud_employee.get_employee_by_user_id(db, user.id)
        if employee:
            if employee.permissions:
                permissions = EmployeePermissions(**employee.permissions)
                assigned = permissions.assigned_client_ids
                if assigned is not None and len(assigned) > 0:
                    return set(assigned)
            from app.db.models.user import User
            business_owner = db.query(User).filter(User.id == employee.business_owner_id).first()
            if business_owner:
                business_id = getattr(business_owner, 'business_id', None)
                if business_id:
                    return get_business_client_ids(db, business_id)
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
        return True
    if user.role == "employee":
        if visit.specialist_id == user.id:
            return True
        return check_client_access(db, user, visit.client_id)
    if user.role == "specialist":
        return visit.specialist_id == user.id
    return False
def check_service_access(db: Session, user: User, service: Service) -> bool:
    if user.role == "admin":
        return True
    if user.role == "business_owner":
        business_id = getattr(user, 'business_id', None)
        if business_id and hasattr(service, 'business_id'):
            return service.business_id == business_id
        return True
    if user.role == "employee":
        if hasattr(service, 'specialist_id') and service.specialist_id == user.id:
            return True
        business_id = getattr(user, 'business_id', None)
        if business_id and hasattr(service, 'business_id'):
            return service.business_id == business_id
        return False
    if user.role == "specialist":
        return service.specialist_id == user.id
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
