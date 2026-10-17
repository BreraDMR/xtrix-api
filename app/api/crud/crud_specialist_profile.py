from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import and_
from app.db.models.specialist_profile import SpecialistProfile
from app.db.models.user import User
from app.db.models.specialist_type import SpecialistType, get_category_for_type
from app.schemas.specialist_profile import SpecialistProfileCreate, SpecialistProfileUpdate
def get_specialist_profile_by_user_id(db: Session, user_id: int) -> Optional[SpecialistProfile]:
    return db.query(SpecialistProfile).filter(SpecialistProfile.user_id == user_id).first()
def get_specialist_profile(db: Session, profile_id: int) -> Optional[SpecialistProfile]:
    return db.query(SpecialistProfile).filter(SpecialistProfile.id == profile_id).first()
def create_specialist_profile(
    db: Session, profile_in: SpecialistProfileCreate
) -> SpecialistProfile:
    existing = get_specialist_profile_by_user_id(db, profile_in.user_id)
    if existing:
        raise ValueError(f"Profile already exists for user_id={profile_in.user_id}")
    user = db.query(User).filter(User.id == profile_in.user_id).first()
    if not user:
        raise ValueError(f"User with id={profile_in.user_id} not found")
    if user.role != "specialist":
        raise ValueError(f"User with id={profile_in.user_id} is not a specialist")
    specialist_category = profile_in.specialist_category
    if not specialist_category and profile_in.specialist_type:
        try:
            specialist_type_enum = SpecialistType(profile_in.specialist_type)
            specialist_category = get_category_for_type(specialist_type_enum).value
        except ValueError:
            pass
    if profile_in.specialist_type and not user.specialist_type:
        user.specialist_type = profile_in.specialist_type
    profile_data = profile_in.model_dump(exclude={"user_id"}) if hasattr(profile_in, 'model_dump') else profile_in.model_dump(exclude={"user_id"})
    profile_data["specialist_category"] = specialist_category
    profile = SpecialistProfile(user_id=profile_in.user_id, **profile_data)
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile
def update_specialist_profile(
    db: Session, profile: SpecialistProfile, profile_in: SpecialistProfileUpdate
) -> SpecialistProfile:
    update_data = profile_in.model_dump(exclude_unset=True) if hasattr(profile_in, 'model_dump') else profile_in.model_dump(exclude_unset=True)
    if "specialist_type" in update_data and update_data["specialist_type"]:
        if "specialist_category" not in update_data or not update_data["specialist_category"]:
            try:
                specialist_type_enum = SpecialistType(update_data["specialist_type"])
                update_data["specialist_category"] = get_category_for_type(specialist_type_enum).value
            except ValueError:
                pass
        user = db.query(User).filter(User.id == profile.user_id).first()
        if user:
            user.specialist_type = update_data["specialist_type"]
    for field, value in update_data.items():
        setattr(profile, field, value)
    db.commit()
    db.refresh(profile)
    return profile
def delete_specialist_profile(db: Session, profile: SpecialistProfile) -> None:
    db.delete(profile)
    db.commit()
def list_specialist_profiles(
    db: Session,
    specialist_type: Optional[str] = None,
    specialist_category: Optional[str] = None,
    is_verified: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
) -> List[SpecialistProfile]:
    query = db.query(SpecialistProfile)
    if specialist_type:
        query = query.filter(SpecialistProfile.specialist_type == specialist_type)
    if specialist_category:
        query = query.filter(SpecialistProfile.specialist_category == specialist_category)
    if is_verified:
        query = query.filter(SpecialistProfile.is_verified == is_verified)
    return query.offset(skip).limit(limit).all()
def list_specialists_by_type(
    db: Session,
    specialist_type: str,
    skip: int = 0,
    limit: int = 100,
) -> List[User]:
    query = db.query(User).filter(
        and_(
            User.role == "specialist",
            User.specialist_type == specialist_type,
            User.deleted_at.is_(None),  
        )
    )
    return query.offset(skip).limit(limit).all()
def list_specialists_by_category(
    db: Session,
    specialist_category: str,
    skip: int = 0,
    limit: int = 100,
) -> List[SpecialistProfile]:
    query = db.query(SpecialistProfile).join(User).filter(
        and_(
            SpecialistProfile.specialist_category == specialist_category,
            User.deleted_at.is_(None),  
        )
    )
    return query.offset(skip).limit(limit).all()
