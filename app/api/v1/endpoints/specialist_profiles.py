"""API endpoints for specialist profiles."""
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core.i18n import _, localize_response
from app.api.core.logger import get_logger
from app.api.core.pagination import PaginationParams, PaginatedResponse
from app.api.crud import crud_specialist_profile
from app.db.models.user import User
from app.schemas.specialist_profile import (
    SpecialistProfileCreate,
    SpecialistProfileUpdate,
    SpecialistProfileOut,
)

router = APIRouter()
logger = get_logger("specialist_profiles")


@router.post("/", response_model=SpecialistProfileOut)
def create_specialist_profile(
    profile: SpecialistProfileCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
    locale: str = Depends(deps.get_locale),
):
    """Create a specialist profile."""
    # Check permissions: specialists can only create their own profile, admins can create any
    if current_user.role != "admin" and profile.user_id != current_user.id:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    
    try:
        result = crud_specialist_profile.create_specialist_profile(db, profile)
        logger.info(f"Specialist profile created: user_id={profile.user_id}")
        return localize_response(
            result.dict() if hasattr(result, 'dict') else result, locale
        )
    except ValueError as e:
        logger.warning(f"Failed to create specialist profile: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/me", response_model=SpecialistProfileOut)
def get_my_profile(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("specialist")),
    locale: str = Depends(deps.get_locale),
):
    """Get current user's specialist profile."""
    profile = crud_specialist_profile.get_specialist_profile_by_user_id(db, current_user.id)
    if not profile:
        raise HTTPException(
            status_code=404,
            detail=_("specialist_profile_not_found", locale)
        )
    return localize_response(
        profile.dict() if hasattr(profile, 'dict') else profile, locale
    )


@router.get("/users/{user_id}", response_model=SpecialistProfileOut)
def get_specialist_profile_by_user(
    user_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist", "client")),
    locale: str = Depends(deps.get_locale),
):
    """Get specialist profile by user ID."""
    # Specialists can only view their own profile (unless admin)
    if current_user.role == "specialist" and user_id != current_user.id:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    
    profile = crud_specialist_profile.get_specialist_profile_by_user_id(db, user_id)
    if not profile:
        raise HTTPException(
            status_code=404,
            detail=_("specialist_profile_not_found", locale)
        )
    return localize_response(
        profile.dict() if hasattr(profile, 'dict') else profile, locale
    )


@router.put("/me", response_model=SpecialistProfileOut)
def update_my_profile(
    profile_update: SpecialistProfileUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("specialist")),
    locale: str = Depends(deps.get_locale),
):
    """Update current user's specialist profile."""
    profile = crud_specialist_profile.get_specialist_profile_by_user_id(db, current_user.id)
    if not profile:
        raise HTTPException(
            status_code=404,
            detail=_("specialist_profile_not_found", locale)
        )
    
    try:
        result = crud_specialist_profile.update_specialist_profile(db, profile, profile_update)
        logger.info(f"Specialist profile updated: user_id={current_user.id}")
        return localize_response(
            result.dict() if hasattr(result, 'dict') else result, locale
        )
    except ValueError as e:
        logger.warning(f"Failed to update specialist profile: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/users/{user_id}", response_model=SpecialistProfileOut)
def update_specialist_profile(
    user_id: int,
    profile_update: SpecialistProfileUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin")),
    locale: str = Depends(deps.get_locale),
):
    """Update specialist profile (admin only)."""
    profile = crud_specialist_profile.get_specialist_profile_by_user_id(db, user_id)
    if not profile:
        raise HTTPException(
            status_code=404,
            detail=_("specialist_profile_not_found", locale)
        )
    
    try:
        result = crud_specialist_profile.update_specialist_profile(db, profile, profile_update)
        logger.info(f"Specialist profile updated by admin: user_id={user_id}")
        return localize_response(
            result.dict() if hasattr(result, 'dict') else result, locale
        )
    except ValueError as e:
        logger.warning(f"Failed to update specialist profile: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/", response_model=List[SpecialistProfileOut])
def list_specialist_profiles(
    specialist_type: Optional[str] = Query(None, description="Filter by specialist type"),
    specialist_category: Optional[str] = Query(None, description="Filter by category: beauty, wellness, healthcare"),
    is_verified: Optional[str] = Query(None, description="Filter by verification status: pending, verified, rejected"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist", "client")),
    locale: str = Depends(deps.get_locale),
):
    """List specialist profiles with optional filters."""
    profiles = crud_specialist_profile.list_specialist_profiles(
        db,
        specialist_type=specialist_type,
        specialist_category=specialist_category,
        is_verified=is_verified,
        skip=skip,
        limit=limit,
    )
    
    return [
        localize_response(
            profile.dict() if hasattr(profile, 'dict') else profile, locale
        )
        for profile in profiles
    ]


@router.get("/types", response_model=dict)
def get_available_specialist_types(
    locale: str = Depends(deps.get_locale),
):
    """Get list of available specialist types and categories."""
    from app.db.models.specialist_type import (
        SpecialistType,
        SpecialistCategory,
        SPECIALIST_TYPE_TO_CATEGORY,
    )
    
    types_by_category = {
        "beauty": [],
        "wellness": [],
        "healthcare": [],
    }
    
    for spec_type in SpecialistType:
        category = SPECIALIST_TYPE_TO_CATEGORY.get(spec_type, SpecialistCategory.BEAUTY)
        types_by_category[category.value].append(spec_type.value)
    
    return {
        "categories": {
            "beauty": {
                "name": _("category_beauty", locale),
                "types": types_by_category["beauty"],
            },
            "wellness": {
                "name": _("category_wellness", locale),
                "types": types_by_category["wellness"],
            },
            "healthcare": {
                "name": _("category_healthcare", locale),
                "types": types_by_category["healthcare"],
            },
        }
    }




