from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.core.booking_slug import slugify_business_name
from app.api.core.clock import utcnow
from app.db.models.business import Business
from app.db.models.public_booking import PublicBookingSettings
from app.schemas.public_booking import PublicBookingSettingsUpdate


class PublicSlugConflict(Exception):
    """The requested slug belongs to another business."""


def _taken(db: Session, slug: str, exclude_id: Optional[int] = None) -> bool:
    query = db.query(PublicBookingSettings.id).filter(PublicBookingSettings.slug == slug)
    if exclude_id:
        query = query.filter(PublicBookingSettings.id != exclude_id)
    return query.first() is not None


def unique_slug(db: Session, base: str) -> str:
    candidate = base
    suffix = 2
    while _taken(db, candidate):
        tail = f"-{suffix}"
        candidate = f"{base[:64 - len(tail)].rstrip('-')}{tail}"
        suffix += 1
    return candidate


def get_settings(db: Session, business_id: int) -> Optional[PublicBookingSettings]:
    return (
        db.query(PublicBookingSettings)
        .filter(PublicBookingSettings.business_id == business_id)
        .first()
    )


def get_enabled_by_slug(db: Session, slug: str) -> Optional[PublicBookingSettings]:
    """Only published rows resolve. Disabled and unknown slugs look the same to a guest."""
    return (
        db.query(PublicBookingSettings)
        .join(Business, PublicBookingSettings.business_id == Business.id)
        .filter(
            PublicBookingSettings.slug == slug,
            PublicBookingSettings.is_enabled.is_(True),
            Business.deleted_at.is_(None),
        )
        .first()
    )


def get_or_create_settings(db: Session, business_id: int) -> Optional[PublicBookingSettings]:
    existing = get_settings(db, business_id)
    if existing:
        return existing

    business = (
        db.query(Business)
        .filter(Business.id == business_id, Business.deleted_at.is_(None))
        .first()
    )
    if not business:
        return None

    for _attempt in range(3):
        settings = PublicBookingSettings(
            business_id=business_id,
            slug=unique_slug(db, slugify_business_name(business.name)),
            is_enabled=False,
        )
        db.add(settings)
        try:
            db.commit()
            db.refresh(settings)
            return settings
        except IntegrityError:
            # another request created the row (or took the slug) between the check and insert
            db.rollback()
            existing = get_settings(db, business_id)
            if existing:
                return existing
    raise RuntimeError("Could not allocate a public booking slug")


def update_settings(
    db: Session,
    settings: PublicBookingSettings,
    update: PublicBookingSettingsUpdate,
) -> PublicBookingSettings:
    data = update.model_dump(exclude_unset=True)
    slug = data.get("slug")
    if slug and slug != settings.slug:
        if _taken(db, slug, exclude_id=settings.id):
            raise PublicSlugConflict()
        settings.slug = slug
    if "is_enabled" in data:
        settings.is_enabled = bool(data["is_enabled"])
    settings.updated_at = utcnow()
    db.add(settings)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise PublicSlugConflict()
    db.refresh(settings)
    return settings
