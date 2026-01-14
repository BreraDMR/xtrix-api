"""
Модуль для синхронизации контактов из базы данных в SendPulse.
"""
from typing import Optional, List, Dict, Any
from datetime import datetime
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.api.core.logger import logger
from app.api.core.sendpulse import get_sendpulse_client
from app.db.models.client import Client
from app.db.models.user import User
from app.db.models.business import Business
from app.db.models.client_business_relation import ClientBusinessRelation


def sync_client_to_sendpulse(
    db: Session,
    client_id: int,
    address_book_id: Optional[int] = None,
) -> bool:
    """
    Синхронизировать одного клиента в SendPulse.
    
    Args:
        db: Сессия базы данных
        client_id: ID клиента
        address_book_id: ID адресной книги SendPulse (опционально)
    
    Returns:
        True если успешно синхронизировано, False в противном случае
    """
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        logger.warning(f"Client {client_id} not found")
        return False
    
    if client.deleted_at or client.archived_at:
        logger.info(f"Client {client_id} is deleted or archived, skipping sync")
        return False
    
    # Получаем бизнесы, с которыми связан клиент
    business_relations = db.query(ClientBusinessRelation).filter(
        ClientBusinessRelation.client_id == client_id
    ).all()
    
    variables = {
        "full_name": client.full_name,
    }
    
    if client.birth_date:
        variables["birth_date"] = client.birth_date.isoformat()
    
    if client.gender:
        variables["gender"] = client.gender
    
    # Добавляем информацию о бизнесах
    business_names = []
    for relation in business_relations:
        business = db.query(Business).filter(Business.id == relation.business_id).first()
        if business:
            business_names.append(business.name)
    
    if business_names:
        variables["businesses"] = ", ".join(business_names)
    
    try:
        client_sp = get_sendpulse_client()
        success = client_sp.create_or_update_contact(
            email=client.contact_email,
            phone=client.contact_phone,
            name=client.full_name,
            address_book_id=address_book_id,
            variables=variables,
        )
        
        if success:
            logger.info(f"Client {client_id} ({client.full_name}) synced to SendPulse")
        else:
            logger.warning(f"Failed to sync client {client_id} to SendPulse")
        
        return success
        
    except Exception as e:
        logger.error(f"Error syncing client {client_id} to SendPulse: {e}")
        return False


def sync_user_to_sendpulse(
    db: Session,
    user_id: int,
    address_book_id: Optional[int] = None,
) -> bool:
    """
    Синхронизировать одного пользователя в SendPulse.
    
    Args:
        db: Сессия базы данных
        user_id: ID пользователя
        address_book_id: ID адресной книги SendPulse (опционально)
    
    Returns:
        True если успешно синхронизировано, False в противном случае
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        logger.warning(f"User {user_id} not found")
        return False
    
    if user.deleted_at or user.archived_at:
        logger.info(f"User {user_id} is deleted or archived, skipping sync")
        return False
    
    variables = {
        "full_name": user.full_name,
        "role": user.role,
    }
    
    if user.specialist_type:
        variables["specialist_type"] = user.specialist_type
    
    if user.business_id:
        business = db.query(Business).filter(Business.id == user.business_id).first()
        if business:
            variables["business"] = business.name
    
    try:
        client_sp = get_sendpulse_client()
        success = client_sp.create_or_update_contact(
            email=user.email,
            phone=user.phone,
            name=user.full_name,
            address_book_id=address_book_id,
            variables=variables,
        )
        
        if success:
            logger.info(f"User {user_id} ({user.full_name}) synced to SendPulse")
        else:
            logger.warning(f"Failed to sync user {user_id} to SendPulse")
        
        return success
        
    except Exception as e:
        logger.error(f"Error syncing user {user_id} to SendPulse: {e}")
        return False


def sync_all_clients_to_sendpulse(
    address_book_id: Optional[int] = None,
    limit: Optional[int] = None,
) -> Dict[str, int]:
    """
    Синхронизировать всех клиентов в SendPulse.
    
    Args:
        address_book_id: ID адресной книги SendPulse (опционально)
        limit: Максимальное количество клиентов для синхронизации (опционально)
    
    Returns:
        Словарь с результатами: {"synced": int, "failed": int, "skipped": int}
    """
    db: Session = SessionLocal()
    results = {"synced": 0, "failed": 0, "skipped": 0}
    
    try:
        query = db.query(Client).filter(
            Client.deleted_at.is_(None),
            Client.archived_at.is_(None),
        )
        
        if limit:
            query = query.limit(limit)
        
        clients = query.all()
        
        logger.info(f"Starting sync of {len(clients)} clients to SendPulse")
        
        for client in clients:
            # Пропускаем клиентов без email и телефона
            if not client.contact_email and not client.contact_phone:
                results["skipped"] += 1
                continue
            
            success = sync_client_to_sendpulse(db, client.id, address_book_id)
            if success:
                results["synced"] += 1
            else:
                results["failed"] += 1
        
        logger.info(f"Sync completed: {results}")
        return results
        
    except Exception as e:
        logger.error(f"Error syncing clients to SendPulse: {e}", exc_info=True)
        raise
    finally:
        db.close()


def sync_all_users_to_sendpulse(
    address_book_id: Optional[int] = None,
    limit: Optional[int] = None,
) -> Dict[str, int]:
    """
    Синхронизировать всех пользователей в SendPulse.
    
    Args:
        address_book_id: ID адресной книги SendPulse (опционально)
        limit: Максимальное количество пользователей для синхронизации (опционально)
    
    Returns:
        Словарь с результатами: {"synced": int, "failed": int, "skipped": int}
    """
    db: Session = SessionLocal()
    results = {"synced": 0, "failed": 0, "skipped": 0}
    
    try:
        query = db.query(User).filter(
            User.deleted_at.is_(None),
            User.archived_at.is_(None),
        )
        
        if limit:
            query = query.limit(limit)
        
        users = query.all()
        
        logger.info(f"Starting sync of {len(users)} users to SendPulse")
        
        for user in users:
            # Пропускаем пользователей без email и телефона
            if not user.email and not user.phone:
                results["skipped"] += 1
                continue
            
            success = sync_user_to_sendpulse(db, user.id, address_book_id)
            if success:
                results["synced"] += 1
            else:
                results["failed"] += 1
        
        logger.info(f"Sync completed: {results}")
        return results
        
    except Exception as e:
        logger.error(f"Error syncing users to SendPulse: {e}", exc_info=True)
        raise
    finally:
        db.close()


def sync_all_contacts_to_sendpulse(
    address_book_id: Optional[int] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Синхронизировать всех контактов (клиентов и пользователей) в SendPulse.
    
    Args:
        address_book_id: ID адресной книги SendPulse (опционально)
        limit: Максимальное количество контактов для синхронизации (опционально)
    
    Returns:
        Словарь с результатами синхронизации
    """
    logger.info("Starting sync of all contacts to SendPulse")
    
    clients_results = sync_all_clients_to_sendpulse(address_book_id, limit)
    users_results = sync_all_users_to_sendpulse(address_book_id, limit)
    
    total_results = {
        "clients": clients_results,
        "users": users_results,
        "total": {
            "synced": clients_results["synced"] + users_results["synced"],
            "failed": clients_results["failed"] + users_results["failed"],
            "skipped": clients_results["skipped"] + users_results["skipped"],
        }
    }
    
    logger.info(f"All contacts sync completed: {total_results}")
    return total_results
