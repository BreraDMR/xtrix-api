from typing import TypeVar
from sqlalchemy.orm import Session, Query
from app.api.core.clock import utcnow
T = TypeVar('T')
def soft_delete(db: Session, instance: T) -> T:
    if hasattr(instance, 'deleted_at'):
        instance.deleted_at = utcnow()
        db.add(instance)
        db.commit()
        db.refresh(instance)
    return instance
def restore_soft_deleted(db: Session, instance: T) -> T:
    if hasattr(instance, 'deleted_at'):
        instance.deleted_at = None
        db.add(instance)
        db.commit()
        db.refresh(instance)
    return instance
def hard_delete(db: Session, instance: T) -> None:
    db.delete(instance)
    db.commit()
def filter_active(query: Query) -> Query:
    model = query.column_descriptions[0]['entity'] if query.column_descriptions else None
    if model and hasattr(model, 'deleted_at'):
        return query.filter(model.deleted_at.is_(None))
    return query
def filter_including_deleted(query: Query) -> Query:
    return query
def filter_only_deleted(query: Query) -> Query:
    model = query.column_descriptions[0]['entity'] if query.column_descriptions else None
    if model and hasattr(model, 'deleted_at'):
        return query.filter(model.deleted_at.isnot(None))
    return query.filter(False)  
def is_deleted(instance: T) -> bool:
    return hasattr(instance, 'deleted_at') and instance.deleted_at is not None
