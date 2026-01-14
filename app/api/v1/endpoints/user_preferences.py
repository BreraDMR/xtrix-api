"""User preferences endpoints."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api import deps
from app.db.models.user import User
from app.schemas.user_preferences import UserPreferencesOut, UserPreferencesUpdate
from app.api.crud import crud_user_preferences

router = APIRouter()


@router.get("/me", response_model=UserPreferencesOut, summary="Get my preferences")
def get_my_preferences(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    """Получить настройки текущего пользователя."""
    preferences = crud_user_preferences.get_or_create_user_preferences(db, current_user.id)
    return UserPreferencesOut.model_validate(preferences)


@router.put("/me", response_model=UserPreferencesOut, summary="Update my preferences")
def update_my_preferences(
    preferences_update: UserPreferencesUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    """Обновить настройки текущего пользователя."""
    preferences = crud_user_preferences.get_or_create_user_preferences(db, current_user.id)
    updated_preferences = crud_user_preferences.update_user_preferences(db, preferences, preferences_update)
    return UserPreferencesOut.model_validate(updated_preferences)
