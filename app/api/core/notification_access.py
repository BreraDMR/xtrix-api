"""Owner-level access to a business's notification settings and delivery log.

Notifications are configured for the whole business, so staff can't read or change them
even when they are allowed to manage clients and visits.
"""
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.core.i18n import _
from app.api.core.permissions import get_staff_record, get_user_business_id
from app.db.models.user import User


def notification_business_id(db: Session, user: User, locale: str) -> int:
    allowed = user.role == "admin" or (
        user.role in ("business_owner", "specialist") and not get_staff_record(db, user)
    )
    if not allowed:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    business_id = get_user_business_id(user, db)
    if not business_id:
        raise HTTPException(status_code=404, detail=_("business_not_found", locale))
    return business_id
