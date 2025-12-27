"""Функции проверки доступа и прав пользователей."""
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
    """Проверяет, может ли пользователь управлять сотрудниками."""
    return user.role in ["admin", "business_owner"]


def can_view_financials(user: User, db: Session) -> bool:
    """Проверяет, может ли пользователь просматривать финансовую информацию."""
    if user.role == "admin":
        return True
    
    if user.role == "business_owner":
        return True
    
    if user.role == "employee":
        # Проверяем права сотрудника
        employee = crud_employee.get_employee_by_user_id(db, user.id)
        if employee and employee.permissions:
            permissions = EmployeePermissions(**employee.permissions)
            return permissions.can_view_financials
    
    # specialist по умолчанию может видеть свои финансы
    return True


def can_manage_services(user: User, db: Session) -> bool:
    """Проверяет, может ли пользователь управлять услугами."""
    if user.role in ["admin", "business_owner", "specialist"]:
        return True
    
    if user.role == "employee":
        employee = crud_employee.get_employee_by_user_id(db, user.id)
        if employee and employee.permissions:
            permissions = EmployeePermissions(**employee.permissions)
            return permissions.can_manage_services
    
    return False


def can_create_visits(user: User, db: Session) -> bool:
    """Проверяет, может ли пользователь создавать визиты."""
    if user.role in ["admin", "business_owner", "specialist"]:
        return True
    
    if user.role == "employee":
        employee = crud_employee.get_employee_by_user_id(db, user.id)
        if employee and employee.permissions:
            permissions = EmployeePermissions(**employee.permissions)
            return permissions.can_create_visits
    
    return False


def can_edit_clients(user: User, db: Session) -> bool:
    """Проверяет, может ли пользователь редактировать клиентов."""
    if user.role in ["admin", "business_owner", "specialist"]:
        return True
    
    if user.role == "employee":
        employee = crud_employee.get_employee_by_user_id(db, user.id)
        if employee and employee.permissions:
            permissions = EmployeePermissions(**employee.permissions)
            return permissions.can_edit_clients
    
    return False


def get_business_client_ids(db: Session, business_id: int) -> Optional[Set[int]]:
    """Получить все client_id, связанные с бизнесом."""
    if business_id is None:
        return None
    
    relations = db.query(ClientBusinessRelation.client_id).filter(
        ClientBusinessRelation.business_id == business_id
    ).all()
    
    return {rel[0] for rel in relations}


def get_specialist_client_ids(db: Session, specialist_id: int, business_id: Optional[int] = None) -> Set[int]:
    """Получить все client_id, связанные со специалистом."""
    client_ids = set()
    
    # Через ClientSpecialistRelation
    relations = db.query(ClientSpecialistRelation.client_id).filter(
        ClientSpecialistRelation.specialist_id == specialist_id
    )
    if business_id:
        relations = relations.filter(ClientSpecialistRelation.business_id == business_id)
    
    client_ids.update({rel[0] for rel in relations.all()})
    
    # Через owner_id (обратная совместимость)
    owner_clients = db.query(Client.id).filter(Client.owner_id == specialist_id).all()
    client_ids.update({c[0] for c in owner_clients})
    
    return client_ids


def get_accessible_client_ids(db: Session, user: User) -> Optional[Set[int]]:
    """
    Возвращает множество client_id, к которым у пользователя есть доступ.
    None означает доступ ко всем клиентам (для admin).
    """
    if user.role == "admin":
        return None  # Доступ ко всем
    
    if user.role == "business_owner":
        # Все клиенты бизнеса
        business_id = getattr(user, 'business_id', None)
        if business_id:
            return get_business_client_ids(db, business_id)
        return set()  # Нет бизнеса = нет клиентов
    
    if user.role == "employee":
        # Клиенты через Employee.permissions
        employee = crud_employee.get_employee_by_user_id(db, user.id)
        if employee:
            if employee.permissions:
                permissions = EmployeePermissions(**employee.permissions)
                assigned = permissions.assigned_client_ids
                if assigned is not None and len(assigned) > 0:
                    # Назначенные клиенты
                    return set(assigned)
            
            # Если assigned_client_ids = None или [], то все клиенты бизнеса
            # Получаем business_id от business_owner через запрос к БД
            from app.db.models.user import User
            business_owner = db.query(User).filter(User.id == employee.business_owner_id).first()
            if business_owner:
                business_id = getattr(business_owner, 'business_id', None)
                if business_id:
                    return get_business_client_ids(db, business_id)
        
        return set()  # Нет записи employee = нет доступа
    
    # specialist - свои клиенты
    if user.role == "specialist":
        business_id = getattr(user, 'business_id', None)
        return get_specialist_client_ids(db, user.id, business_id)
    
    return set()  # Нет доступа


def check_client_access(db: Session, user: User, client_id: int) -> bool:
    """Проверяет, есть ли у пользователя доступ к конкретному клиенту."""
    if user.role == "admin":
        return True
    
    accessible_ids = get_accessible_client_ids(db, user)
    if accessible_ids is None:  # admin
        return True
    
    return client_id in accessible_ids


def check_visit_access(db: Session, user: User, visit: Visit) -> bool:
    """Проверяет, есть ли у пользователя доступ к визиту."""
    if user.role == "admin":
        return True
    
    if user.role == "business_owner":
        # Визиты бизнеса
        business_id = getattr(user, 'business_id', None)
        if business_id and hasattr(visit, 'business_id'):
            return visit.business_id == business_id
        # Для обратной совместимости: визиты специалистов бизнеса
        # (это можно улучшить, но пока так)
        return True
    
    if user.role == "employee":
        # Визиты сотрудника или назначенных ему клиентов
        if visit.specialist_id == user.id:
            return True
        # Проверяем доступ к клиенту
        return check_client_access(db, user, visit.client_id)
    
    # specialist - только свои визиты
    if user.role == "specialist":
        return visit.specialist_id == user.id
    
    return False


def check_service_access(db: Session, user: User, service: Service) -> bool:
    """Проверяет, есть ли у пользователя доступ к услуге."""
    if user.role == "admin":
        return True
    
    if user.role == "business_owner":
        # Услуги бизнеса
        business_id = getattr(user, 'business_id', None)
        if business_id and hasattr(service, 'business_id'):
            return service.business_id == business_id
        # Для обратной совместимости
        return True
    
    if user.role == "employee":
        # Услуги могут быть доступны через бизнес
        # Пока проверяем только свои услуги (если есть)
        if hasattr(service, 'specialist_id') and service.specialist_id == user.id:
            return True
        # Или услуги бизнеса (если разрешено)
        business_id = getattr(user, 'business_id', None)
        if business_id and hasattr(service, 'business_id'):
            return service.business_id == business_id
        return False
    
    # specialist - только свои услуги
    if user.role == "specialist":
        return service.specialist_id == user.id
    
    return False


def get_filtered_specialist_id(user: User) -> Optional[int]:
    """
    Возвращает specialist_id для фильтрации данных.
    None означает "все специалисты" (для admin/business_owner).
    """
    if user.role == "admin":
        return None  # Все специалисты
    
    if user.role == "business_owner":
        return None  # Все специалисты бизнеса (фильтрация по business_id)
    
    # specialist и employee видят только свои данные
    return user.id

