from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.core.i18n import _
from app.api.core.permissions import get_staff_record, get_user_business_id
from app.db.models.user import User


def manage_public_booking_business_id(db: Session, user: User, locale: str) -> int:
    """The business whose public link this user may publish.

    The link is an owner-level switch: staff of a business can't turn it on or rename it,
    even when they are allowed to manage services.
    """
    allowed = user.role == "admin" or (
        user.role in ("business_owner", "specialist") and not get_staff_record(db, user)
    )
    business_id = get_user_business_id(user, db) if allowed else None
    if not allowed:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    if not business_id:
        raise HTTPException(status_code=404, detail=_("business_not_found", locale))
    return business_id
