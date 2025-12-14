"""Soft delete utilities."""
from datetime import datetime
from typing import Optional, Type, TypeVar
from sqlalchemy.orm import Session, Query
from sqlalchemy import Column, DateTime

T = TypeVar('T')


def soft_delete(db: Session, instance: T) -> T:
    """Soft delete an instance by setting deleted_at timestamp."""
    if hasattr(instance, 'deleted_at'):
        instance.deleted_at = datetime.utcnow()
        db.add(instance)
        db.commit()
        db.refresh(instance)
    return instance


def restore_soft_deleted(db: Session, instance: T) -> T:
    """Restore a soft-deleted instance."""
    if hasattr(instance, 'deleted_at'):
        instance.deleted_at = None
        db.add(instance)
        db.commit()
        db.refresh(instance)
    return instance


def hard_delete(db: Session, instance: T) -> None:
    """Permanently delete an instance from database."""
    db.delete(instance)
    db.commit()


def filter_active(query: Query) -> Query:
    """Filter query to exclude soft-deleted records."""
    # Check if model has deleted_at column
    model = query.column_descriptions[0]['entity'] if query.column_descriptions else None
    if model and hasattr(model, 'deleted_at'):
        return query.filter(model.deleted_at.is_(None))
    return query


def filter_including_deleted(query: Query) -> Query:
    """Return query including soft-deleted records."""
    return query


def filter_only_deleted(query: Query) -> Query:
    """Filter query to show only soft-deleted records."""
    model = query.column_descriptions[0]['entity'] if query.column_descriptions else None
    if model and hasattr(model, 'deleted_at'):
        return query.filter(model.deleted_at.isnot(None))
    return query.filter(False)  # Return empty if no deleted_at


def is_deleted(instance: T) -> bool:
    """Check if instance is soft-deleted."""
    return hasattr(instance, 'deleted_at') and instance.deleted_at is not None









