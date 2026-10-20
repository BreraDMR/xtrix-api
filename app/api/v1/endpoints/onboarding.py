from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core import onboarding as onboarding_core
from app.api.crud import crud_user_preferences
from app.db.models.user import User
from app.schemas.onboarding import (
    ONBOARDING_HINT_KEYS,
    OnboardingStateOut,
    OnboardingUpdate,
)

router = APIRouter()


def _state(db: Session, user: User, preferences) -> OnboardingStateOut:
    snapshot = onboarding_core.compute_onboarding(db, user)
    stored_hints = preferences.onboarding_dismissed_hints or []
    hints = [hint for hint in ONBOARDING_HINT_KEYS if hint in stored_hints]
    return OnboardingStateOut(
        persona=snapshot.persona,
        steps=snapshot.steps,
        completed_steps=snapshot.completed_steps,
        total_steps=snapshot.total_steps,
        progress_percent=snapshot.progress_percent,
        next_step=snapshot.next_step,
        onboarding_version=preferences.onboarding_version or onboarding_core.ONBOARDING_VERSION,
        tour_version=onboarding_core.ONBOARDING_TOUR_VERSION,
        tour_completed=bool(preferences.onboarding_tour_completed),
        tour_dismissed=bool(preferences.onboarding_tour_dismissed),
        welcome_dismissed=bool(preferences.onboarding_welcome_dismissed),
        checklist_dismissed=bool(preferences.onboarding_checklist_dismissed),
        dismissed_hints=hints,
    )


@router.get("/me", response_model=OnboardingStateOut, summary="Get my first-run checklist")
def get_my_onboarding(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    preferences = crud_user_preferences.get_or_create_user_preferences(db, current_user.id)
    return _state(db, current_user, preferences)


@router.patch("/me", response_model=OnboardingStateOut, summary="Save my first-run choices")
def update_my_onboarding(
    payload: OnboardingUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    preferences = crud_user_preferences.get_or_create_user_preferences(db, current_user.id)
    changes = payload.model_dump(exclude_unset=True)

    if changes.get("tour_completed"):
        preferences.onboarding_tour_completed = True
        preferences.onboarding_tour_dismissed = False
        preferences.onboarding_tour_version = onboarding_core.ONBOARDING_TOUR_VERSION
    elif "tour_completed" in changes:
        # lets Settings offer the tour again without touching business data
        preferences.onboarding_tour_completed = False
    if changes.get("tour_dismissed"):
        preferences.onboarding_tour_dismissed = True
        preferences.onboarding_tour_completed = False
        preferences.onboarding_tour_version = onboarding_core.ONBOARDING_TOUR_VERSION
    elif "tour_dismissed" in changes:
        preferences.onboarding_tour_dismissed = False
    if "welcome_dismissed" in changes:
        preferences.onboarding_welcome_dismissed = bool(changes["welcome_dismissed"])
    if "checklist_dismissed" in changes:
        preferences.onboarding_checklist_dismissed = bool(changes["checklist_dismissed"])
    if "dismissed_hints" in changes:
        preferences.onboarding_dismissed_hints = changes["dismissed_hints"] or []
    preferences.onboarding_version = onboarding_core.ONBOARDING_VERSION

    db.add(preferences)
    db.commit()
    db.refresh(preferences)
    return _state(db, current_user, preferences)
