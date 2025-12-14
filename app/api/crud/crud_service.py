from typing import Optional, Tuple

from sqlalchemy.orm import Session, joinedload

from app.api.core.pagination import PaginationParams
from app.api.core.soft_delete import filter_active, soft_delete
from app.db.models.service import Service
from app.schemas.service import ServiceCreate, ServiceUpdate


def create_service(db: Session, service_in: ServiceCreate) -> Service:
    service = Service(**service_in.dict())
    db.add(service)
    db.commit()
    db.refresh(service)
    return service


def update_service(db: Session, service: Service, update: ServiceUpdate) -> Service:
    for field, value in update.dict(exclude_unset=True).items():
        setattr(service, field, value)
    db.add(service)
    db.commit()
    db.refresh(service)
    return service


def get_service(db: Session, service_id: int, include_deleted: bool = False) -> Optional[Service]:
    """Get service with optimized query and optional soft-deleted inclusion."""
    query = db.query(Service).options(joinedload(Service.specialist))
    if not include_deleted:
        query = filter_active(query)
    return query.filter(Service.id == service_id).first()


def list_services(
    db: Session,
    specialist_id: Optional[int] = None,
    active_only: bool = False,
    pagination: Optional[PaginationParams] = None,
    include_deleted: bool = False,
    include_archived: bool = False,
) -> Tuple[list[Service], int]:
    """List services with optional pagination. Returns (items, total_count)."""
    # Optimized query with joinedload for relationships
    q = db.query(Service).options(joinedload(Service.specialist))
    
    # Filter out deleted and archived by default
    if not include_deleted:
        q = filter_active(q)
    if not include_archived:
        q = q.filter(Service.archived_at.is_(None))
    
    # Apply filters
    if specialist_id:
        q = q.filter(Service.specialist_id == specialist_id)
    if active_only:
        q = q.filter(Service.is_active.is_(True))
    
    # Get total count before pagination
    total = q.count()
    
    # Apply pagination if provided
    if pagination:
        q = q.offset(pagination.offset).limit(pagination.limit)
    
    items = q.all()
    return items, total



