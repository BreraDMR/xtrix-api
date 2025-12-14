"""Data archiving utilities."""
from datetime import datetime, timedelta
from typing import Type, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.api.core.soft_delete import filter_active
from app.db.models.client import Client
from app.db.models.visit import Visit
from app.db.models.service import Service
from app.db.models.user import User


def archive_old_clients(
    db: Session,
    days_old: int = 365,
    owner_id: Optional[int] = None,
) -> int:
    """
    Archive clients that haven't been updated in specified days.
    Returns number of archived clients.
    """
    cutoff_date = datetime.utcnow() - timedelta(days=days_old)
    
    query = db.query(Client).filter(
        and_(
            Client.archived_at.is_(None),
            Client.deleted_at.is_(None),
            Client.created_at < cutoff_date,
        )
    )
    
    if owner_id:
        query = query.filter(Client.owner_id == owner_id)
    
    clients = query.all()
    archived_count = 0
    
    for client in clients:
        # Check if client has recent visits
        recent_visits = db.query(Visit).filter(
            and_(
                Visit.client_id == client.id,
                Visit.date_start > cutoff_date,
                Visit.deleted_at.is_(None),
            )
        ).count()
        
        # Only archive if no recent visits
        if recent_visits == 0:
            client.archived_at = datetime.utcnow()
            archived_count += 1
    
    db.commit()
    return archived_count


def archive_old_visits(
    db: Session,
    days_old: int = 365,
    status: Optional[str] = "completed",
) -> int:
    """
    Archive old completed visits.
    Returns number of archived visits.
    """
    cutoff_date = datetime.utcnow() - timedelta(days=days_old)
    
    query = db.query(Visit).filter(
        and_(
            Visit.archived_at.is_(None),
            Visit.deleted_at.is_(None),
            Visit.date_start < cutoff_date,
        )
    )
    
    if status:
        query = query.filter(Visit.status == status)
    
    visits = query.all()
    for visit in visits:
        visit.archived_at = datetime.utcnow()
    
    db.commit()
    return len(visits)


def archive_old_services(
    db: Session,
    days_old: int = 180,
    inactive_only: bool = True,
) -> int:
    """
    Archive old inactive services.
    Returns number of archived services.
    """
    cutoff_date = datetime.utcnow() - timedelta(days=days_old)
    
    query = db.query(Service).filter(
        and_(
            Service.archived_at.is_(None),
            Service.deleted_at.is_(None),
        )
    )
    
    if inactive_only:
        query = query.filter(Service.is_active == False)
    
    if hasattr(Service, 'created_at'):
        query = query.filter(Service.created_at < cutoff_date)
    
    services = query.all()
    for service in services:
        # Check if service has recent visits
        recent_visits = db.query(Visit).filter(
            and_(
                Visit.service_id == service.id,
                Visit.date_start > cutoff_date,
                Visit.deleted_at.is_(None),
            )
        ).count()
        
        if recent_visits == 0:
            service.archived_at = datetime.utcnow()
    
    db.commit()
    return len([s for s in services if s.archived_at])


def unarchive(
    db: Session,
    model_class: Type,
    instance_id: int,
) -> bool:
    """Unarchive an archived record."""
    instance = db.query(model_class).filter(
        model_class.id == instance_id,
        model_class.archived_at.isnot(None),
    ).first()
    
    if instance:
        instance.archived_at = None
        db.commit()
        return True
    return False


def get_archived_count(
    db: Session,
    model_class: Type,
    days_old: Optional[int] = None,
) -> int:
    """Get count of archived records."""
    query = db.query(model_class).filter(
        model_class.archived_at.isnot(None)
    )
    
    if days_old:
        cutoff_date = datetime.utcnow() - timedelta(days=days_old)
        query = query.filter(model_class.archived_at < cutoff_date)
    
    return query.count()









