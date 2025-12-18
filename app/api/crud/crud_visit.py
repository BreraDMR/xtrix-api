from datetime import datetime
from typing import Optional, Tuple

from sqlalchemy.orm import Session, joinedload

from app.api.core.pagination import PaginationParams
from app.api.core.soft_delete import filter_active, soft_delete
from app.db.models.visit import Visit
from app.schemas.visit import VisitCreate, VisitUpdate


def create_visit(db: Session, visit_in: VisitCreate) -> Visit:
    try:
        visit = Visit(**visit_in.dict())
        db.add(visit)
        db.commit()
        db.refresh(visit)
        return visit
    except Exception as e:
        db.rollback()
        raise  # Re-raise the exception to be handled by the endpoint


def update_visit(db: Session, visit: Visit, update: VisitUpdate) -> Visit:
    for field, value in update.dict(exclude_unset=True).items():
        setattr(visit, field, value)
    db.add(visit)
    db.commit()
    db.refresh(visit)
    return visit


def get_visit(db: Session, visit_id: int, include_deleted: bool = False) -> Optional[Visit]:
    """Get visit with optimized query and optional soft-deleted inclusion."""
    query = db.query(Visit).options(
        joinedload(Visit.client),
        joinedload(Visit.specialist),
        joinedload(Visit.service),
    )
    if not include_deleted:
        query = filter_active(query)
    return query.filter(Visit.id == visit_id).first()


def list_visits(
    db: Session,
    client_id: Optional[int] = None,
    specialist_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    pagination: Optional[PaginationParams] = None,
    include_deleted: bool = False,
    include_archived: bool = False,
) -> Tuple[list[Visit], int]:
    """List visits with optional pagination. Returns (items, total_count)."""
    # Optimized query with joinedload for relationships
    q = db.query(Visit).options(
        joinedload(Visit.client),
        joinedload(Visit.specialist),
        joinedload(Visit.service),
    )
    
    # Filter out deleted and archived by default
    if not include_deleted:
        q = filter_active(q)
    if not include_archived:
        q = q.filter(Visit.archived_at.is_(None))
    
    # Apply filters
    if client_id:
        q = q.filter(Visit.client_id == client_id)
    if specialist_id:
        q = q.filter(Visit.specialist_id == specialist_id)
    if date_from:
        q = q.filter(Visit.date_start >= date_from)
    if date_to:
        q = q.filter(Visit.date_start <= date_to)
    
    # Get total count before pagination
    total = q.count()
    
    # Apply pagination if provided
    if pagination:
        q = q.offset(pagination.offset).limit(pagination.limit)
    
    items = q.all()
    return items, total



