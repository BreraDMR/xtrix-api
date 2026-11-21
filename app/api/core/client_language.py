"""Which language a new client's messages start in, when nobody picked one.

The salon's language wins: the owner's UI language from preferences (there is no separate
salon setting). A solo specialist has no salon, so their own UI language is next, then the
language of the request, and English when nothing is known - same fallback as the public
booking page.
"""
from typing import Optional

from sqlalchemy.orm import Session

from app.api.core.i18n import SUPPORTED_LOCALES, normalize_locale
from app.db.models.user import User
from app.db.models.user_preferences import UserPreferences


def _ui_language(db: Session, user_id: Optional[int]) -> Optional[str]:
    if not user_id:
        return None
    prefs = db.query(UserPreferences.language).filter(UserPreferences.user_id == user_id).first()
    language = prefs[0] if prefs else None
    return language if language in SUPPORTED_LOCALES else None


def salon_language(db: Session, business_id: Optional[int]) -> Optional[str]:
    if not business_id:
        return None
    owner = (
        db.query(User.id)
        .filter(User.business_id == business_id, User.role == "business_owner", User.deleted_at.is_(None))
        .order_by(User.id)
        .first()
    )
    return _ui_language(db, owner[0]) if owner else None


def default_client_language(
    db: Session, business_id: Optional[int], user: Optional[User] = None, locale: Optional[str] = None
) -> str:
    return (
        salon_language(db, business_id)
        or _ui_language(db, getattr(user, "id", None))
        or normalize_locale(locale)
    )
