from typing import Optional
from sqlalchemy.orm import Session

from app.db.models.user_preferences import UserPreferences
from app.schemas.user_preferences import UserPreferencesUpdate


def get_user_preferences(db: Session, user_id: int) -> Optional[UserPreferences]:
    """Получить настройки пользователя."""
    return db.query(UserPreferences).filter(UserPreferences.user_id == user_id).first()


def get_or_create_user_preferences(db: Session, user_id: int) -> UserPreferences:
    """Получить или создать настройки пользователя."""
    preferences = get_user_preferences(db, user_id)
    if not preferences:
        preferences = UserPreferences(user_id=user_id)
        db.add(preferences)
        db.commit()
        db.refresh(preferences)
    return preferences


def update_user_preferences(
    db: Session,
    preferences: UserPreferences,
    preferences_update: UserPreferencesUpdate
) -> UserPreferences:
    """Обновить настройки пользователя."""
    update_data = preferences_update.model_dump(exclude_unset=True)
    
    for field, value in update_data.items():
        setattr(preferences, field, value)
    
    db.add(preferences)
    db.commit()
    db.refresh(preferences)
    return preferences
