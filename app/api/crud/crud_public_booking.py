import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.api.core.availability import salon_zone
from app.api.core.booking_slug import slugify_business_name
from app.api.core.clock import utcnow
from app.db.models.block_slot import BlockSlot
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.client_business_relation import ClientBusinessRelation
from app.db.models.client_specialist_relation import ClientSpecialistRelation
from app.db.models.employee import Employee
from app.db.models.public_booking import PublicBookingSettings
from app.db.models.schedule_exception import ScheduleException
from app.db.models.service import Service
from app.db.models.service_specialist import ServiceSpecialist
from app.db.models.user import User
from app.db.models.visit import Visit
from app.db.models.visit_item import VisitItem
from app.db.models.work_schedule import WorkSchedule
from app.schemas.public_booking import PublicBookingCreate, PublicBookingSettingsUpdate


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


def business_owner_ids(db: Session, business_id: int) -> set[int]:
    return {
        row[0]
        for row in db.query(User.id)
        .filter(
            User.business_id == business_id,
            User.role.in_(("business_owner", "specialist")),
            User.deleted_at.is_(None),
            User.archived_at.is_(None),
        )
        .all()
    }


def business_specialist_ids(db: Session, business_id: int) -> set[int]:
    """Active people a guest may be booked with: the owner-side users and live employees."""
    owner_ids = business_owner_ids(db, business_id)
    if not owner_ids:
        return set()
    staff_ids = {
        row[0]
        for row in db.query(Employee.employee_user_id)
        .join(User, Employee.employee_user_id == User.id)
        .filter(
            Employee.business_owner_id.in_(owner_ids),
            Employee.is_active.is_(True),
            User.deleted_at.is_(None),
            User.archived_at.is_(None),
        )
        .all()
    }
    return owner_ids | staff_ids


def _service_master_ids(db: Session, services: list[Service], staff_ids: set[int]) -> dict[int, set[int]]:
    if not services:
        return {}
    links: dict[int, set[int]] = {}
    rows = (
        db.query(ServiceSpecialist.service_id, ServiceSpecialist.specialist_id)
        .filter(ServiceSpecialist.service_id.in_([service.id for service in services]))
        .all()
    )
    for service_id, specialist_id in rows:
        links.setdefault(service_id, set()).add(specialist_id)
    return {
        service.id: ({service.specialist_id} | links.get(service.id, set())) & staff_ids
        for service in services
    }


def list_public_services(db: Session, business_id: int) -> list[Service]:
    """Active services with a duration and at least one current master. Nothing else is bookable."""
    staff_ids = business_specialist_ids(db, business_id)
    if not staff_ids:
        return []
    owner_ids = business_owner_ids(db, business_id)
    services = (
        db.query(Service)
        .filter(
            or_(
                Service.business_id == business_id,
                and_(Service.business_id.is_(None), Service.specialist_id.in_(owner_ids)),
            ),
            Service.is_active.is_(True),
            Service.deleted_at.is_(None),
            Service.archived_at.is_(None),
            Service.duration_minutes.isnot(None),
        )
        .order_by(Service.name, Service.id)
        .all()
    )
    masters = _service_master_ids(db, services, staff_ids)
    return [service for service in services if masters.get(service.id)][:500]


def get_public_service(db: Session, business_id: int, service_id: int) -> Optional[Service]:
    staff_ids = business_specialist_ids(db, business_id)
    if not staff_ids:
        return None
    owner_ids = business_owner_ids(db, business_id)
    service = (
        db.query(Service)
        .filter(
            Service.id == service_id,
            or_(
                Service.business_id == business_id,
                and_(Service.business_id.is_(None), Service.specialist_id.in_(owner_ids)),
            ),
            Service.is_active.is_(True),
            Service.deleted_at.is_(None),
            Service.archived_at.is_(None),
            Service.duration_minutes.isnot(None),
        )
        .first()
    )
    if not service:
        return None
    masters = _service_master_ids(db, [service], staff_ids)
    return service if masters.get(service.id) else None


def public_service_master_ids(db: Session, business_id: int, service: Service) -> set[int]:
    staff_ids = business_specialist_ids(db, business_id)
    return _service_master_ids(db, [service], staff_ids).get(service.id, set())


def public_booking_readiness(db: Session, business_id: int) -> tuple[bool, bool]:
    """Whether the published link can actually offer a visit.

    The service must already pass the public allowlist, and one of its masters must have
    a weekly schedule. Anything else would show a guest an empty page or an empty day strip.
    """
    services = list_public_services(db, business_id)
    if not services:
        return False, False
    staff_ids = business_specialist_ids(db, business_id)
    masters = _service_master_ids(db, services, staff_ids)
    master_ids = set().union(*masters.values()) if masters else set()
    if not master_ids:
        return True, False
    has_schedule = (
        db.query(WorkSchedule.id)
        .filter(WorkSchedule.specialist_id.in_(master_ids))
        .first()
        is not None
    )
    return True, has_schedule


def list_public_staff(db: Session, business_id: int, service: Service) -> list[User]:
    master_ids = public_service_master_ids(db, business_id, service)
    if not master_ids:
        return []
    return (
        db.query(User)
        .filter(
            User.id.in_(master_ids),
            User.deleted_at.is_(None),
            User.archived_at.is_(None),
        )
        .order_by(User.full_name, User.id)
        .limit(500)
        .all()
    )


def staff_positions(db: Session, staff_ids: list[int]) -> dict[int, str]:
    if not staff_ids:
        return {}
    rows = (
        db.query(Employee.employee_user_id, Employee.position)
        .filter(
            Employee.employee_user_id.in_(staff_ids),
            Employee.is_active.is_(True),
            Employee.position.isnot(None),
        )
        .all()
    )
    return {user_id: position for user_id, position in rows if position}


def availability_sources(
    db: Session,
    master_ids: list[int],
    date_from: date,
    date_to: date,
    timezone_name: str,
    exclude_visit_id: Optional[int] = None,
):
    """Load what the availability calculation needs for a short date window.

    Visits intentionally aren't filtered by business: one person can't be in two salons
    at once. Their service buffers stretch the occupied range as well.
    """
    schedules: dict[int, list[tuple[int, time, time]]] = {}
    exceptions: dict[int, dict[date, tuple[Optional[time], Optional[time]]]] = {}
    blocks: dict[int, list[tuple[datetime, datetime]]] = {}
    busy: dict[int, list[tuple[datetime, datetime]]] = {}
    if not master_ids:
        return schedules, exceptions, blocks, busy

    zone = salon_zone(timezone_name)
    local_start = datetime.combine(date_from, time.min).replace(tzinfo=zone)
    local_end = datetime.combine(date_to + timedelta(days=1), time.min).replace(tzinfo=zone)
    window_start = local_start.astimezone(timezone.utc).replace(tzinfo=None)
    window_end = local_end.astimezone(timezone.utc).replace(tzinfo=None)
    margin = timedelta(days=2)
    query_start = window_start - margin
    query_end = window_end + margin

    for row in (
        db.query(WorkSchedule)
        .filter(WorkSchedule.specialist_id.in_(master_ids))
        .all()
    ):
        schedules.setdefault(row.specialist_id, []).append(
            (row.weekday, row.start_time, row.end_time)
        )

    for row in (
        db.query(ScheduleException)
        .filter(
            ScheduleException.specialist_id.in_(master_ids),
            ScheduleException.exception_date >= date_from,
            ScheduleException.exception_date <= date_to,
        )
        .all()
    ):
        exceptions.setdefault(row.specialist_id, {})[row.exception_date] = (
            row.start_time,
            row.end_time,
        )

    for row in (
        db.query(BlockSlot)
        .filter(
            BlockSlot.specialist_id.in_(master_ids),
            BlockSlot.start < query_end,
            BlockSlot.end > query_start,
        )
        .all()
    ):
        blocks.setdefault(row.specialist_id, []).append((row.start, row.end))

    items_query = (
        db.query(VisitItem)
        .join(Visit, VisitItem.visit_id == Visit.id)
        .filter(
            VisitItem.specialist_id.in_(master_ids),
            Visit.deleted_at.is_(None),
            Visit.archived_at.is_(None),
            or_(Visit.status.is_(None), Visit.status != "cancelled"),
            VisitItem.date_start < query_end,
            VisitItem.date_end > query_start,
        )
    )
    if exclude_visit_id is not None:
        items_query = items_query.filter(Visit.id != exclude_visit_id)
    for item in items_query.all():
        before = getattr(item, "buffer_before_minutes", 0) or 0
        after = getattr(item, "buffer_after_minutes", 0) or 0
        busy.setdefault(item.specialist_id, []).append(
            (
                item.date_start - timedelta(minutes=before),
                item.date_end + timedelta(minutes=after),
            )
        )

    # only visits without items need the old visit-level fallback
    visits_query = (
        db.query(Visit)
        .options(joinedload(Visit.service))
        .filter(
            Visit.specialist_id.in_(master_ids),
            Visit.deleted_at.is_(None),
            Visit.archived_at.is_(None),
            or_(Visit.status.is_(None), Visit.status != "cancelled"),
            ~Visit.items.any(),
            Visit.date_start < query_end,
            or_(
                Visit.date_end > query_start,
                and_(
                    Visit.date_end.is_(None),
                    Visit.date_start > query_start - timedelta(hours=1),
                ),
            ),
        )
    )
    if exclude_visit_id is not None:
        # a guest rescheduling their own visit must not treat its current time as someone else's
        visits_query = visits_query.filter(Visit.id != exclude_visit_id)
    visits = visits_query.all()
    for visit in visits:
        end = visit.date_end or visit.date_start + timedelta(minutes=60)
        service = visit.service
        before = getattr(service, "buffer_before_minutes", 0) or 0
        after = getattr(service, "buffer_after_minutes", 0) or 0
        busy.setdefault(visit.specialist_id, []).append(
            (
                visit.date_start - timedelta(minutes=before),
                end + timedelta(minutes=after),
            )
        )

    return schedules, exceptions, blocks, busy


def _business_clients_query(db: Session, business_id: int):
    return (
        db.query(Client)
        .join(ClientBusinessRelation, ClientBusinessRelation.client_id == Client.id)
        .filter(
            ClientBusinessRelation.business_id == business_id,
            Client.deleted_at.is_(None),
        )
    )


def find_business_client_by_contact(
    db: Session,
    business_id: int,
    phone: Optional[str],
    email: Optional[str],
) -> Optional[Client]:
    """Match only inside this business; a stranger's contact in another salon is invisible here."""
    query = _business_clients_query(db, business_id)
    if email:
        return query.filter(func.lower(Client.contact_email) == email.lower()).first()
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    expression = Client.contact_phone
    for character in (" ", "-", "(", ")", ".", "/", "+"):
        expression = func.replace(expression, character, "")
    return query.filter(Client.contact_phone.isnot(None), expression == digits).first()


def ensure_client_relations(
    db: Session,
    client_id: int,
    business_id: int,
    specialist_id: int,
) -> None:
    if not db.query(ClientBusinessRelation.id).filter(
        ClientBusinessRelation.client_id == client_id,
        ClientBusinessRelation.business_id == business_id,
    ).first():
        db.add(ClientBusinessRelation(client_id=client_id, business_id=business_id))
    if not db.query(ClientSpecialistRelation.id).filter(
        ClientSpecialistRelation.client_id == client_id,
        ClientSpecialistRelation.specialist_id == specialist_id,
        ClientSpecialistRelation.business_id == business_id,
    ).first():
        db.add(
            ClientSpecialistRelation(
                client_id=client_id,
                specialist_id=specialist_id,
                business_id=business_id,
            )
        )


def find_or_create_client(
    db: Session,
    business_id: int,
    specialist_id: int,
    payload: PublicBookingCreate,
    language: str,
) -> Client:
    client = find_business_client_by_contact(
        db, business_id, payload.phone, payload.email
    )
    if client:
        ensure_client_relations(db, client.id, business_id, specialist_id)
        return client

    client = Client(
        owner_id=None,
        full_name=payload.full_name,
        contact_phone=payload.phone,
        contact_email=payload.email,
        language=language if language in ("en", "ru", "cz") else "en",
    )
    db.add(client)
    db.flush()
    ensure_client_relations(db, client.id, business_id, specialist_id)
    return client
