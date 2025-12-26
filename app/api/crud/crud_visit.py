from datetime import datetime
from typing import Optional, Tuple

from sqlalchemy.orm import Session, joinedload

from app.api.core.pagination import PaginationParams
from app.api.core.soft_delete import filter_active, soft_delete
from app.db.models.visit import Visit
from app.schemas.visit import VisitCreate, VisitUpdate


def create_visit(db: Session, visit_in: VisitCreate, business_id: Optional[int] = None) -> Visit:
    """
    Create a new visit.
    
    Args:
        db: Database session
        visit_in: Visit data
        business_id: Business ID to associate the visit with (if None, will try to get from specialist)
    """
    visit_data = visit_in.dict()
    
    # Set business_id if provided, otherwise try to get it from specialist
    if business_id is None:
        # Try to get business_id from specialist
        from app.db.models.user import User
        specialist = db.query(User).filter(User.id == visit_data.get('specialist_id')).first()
        if specialist and hasattr(specialist, 'business_id') and specialist.business_id:
            business_id = specialist.business_id
    
    if business_id:
        visit_data['business_id'] = business_id
    
    visit = Visit(**visit_data)
    db.add(visit)
    db.commit()
    db.refresh(visit)
    return visit


def update_visit(db: Session, visit: Visit, update: VisitUpdate) -> Visit:
    for field, value in update.dict(exclude_unset=True).items():
        setattr(visit, field, value)
    db.add(visit)
    db.commit()
    db.refresh(visit)
    return visit


def get_visit(
    db: Session, 
    visit_id: int, 
    include_deleted: bool = False,
    business_id: Optional[int] = None  # Для проверки доступа
) -> Optional[Visit]:
    """Get visit with optimized query and optional soft-deleted inclusion."""
    query = db.query(Visit).options(
        joinedload(Visit.client),
        joinedload(Visit.specialist),
        joinedload(Visit.service),
    )
    if not include_deleted:
        query = filter_active(query)
    
    query = query.filter(Visit.id == visit_id)
    
    # Если указан business_id, фильтруем по нему для изоляции
    # Но также показываем записи с NULL business_id для обратной совместимости
    if business_id:
        from sqlalchemy import or_
        query = query.filter(or_(
            Visit.business_id == business_id,
            Visit.business_id.is_(None)  # Показываем старые записи без business_id
        ))
    
    return query.first()


def list_visits(
    db: Session,
    client_id: Optional[int] = None,
    specialist_id: Optional[int] = None,
    business_id: Optional[int] = None,  # Новый параметр для изоляции по бизнесу
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
    # Приоритет: если указан business_id, используем его для изоляции данных
    # Но также показываем записи с NULL business_id для обратной совместимости
    if business_id:
        from sqlalchemy import or_
        q = q.filter(or_(
            Visit.business_id == business_id,
            Visit.business_id.is_(None)  # Показываем старые записи без business_id
        ))
    
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



