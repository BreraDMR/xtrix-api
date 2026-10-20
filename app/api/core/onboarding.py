"""First-run progress derived from real business data.

No checklist flag is stored: a step is done only when the object behind it exists.
Unreleased screens stay unavailable so they neither count towards progress nor lead
to a route that is not there yet.
"""
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.core.permissions import get_staff_record, get_user_business_id
from app.db.models.business import Business
from app.db.models.employee import Employee
from app.db.models.public_booking import PublicBookingSettings
from app.db.models.service import Service
from app.db.models.user import User
from app.db.models.work_schedule import WorkSchedule
from app.schemas.onboarding import OnboardingPersona, OnboardingStep, OnboardingStepKey

ONBOARDING_VERSION = 1
ONBOARDING_TOUR_VERSION = 1

# The schedule editor and the public booking form are routed since ON-14.
SCHEDULE_STEP_READY = True
BOOKING_STEP_READY = True

STEP_ORDER: tuple[OnboardingStepKey, ...] = (
    "business", "service", "staff", "schedule", "booking",
)


@dataclass(frozen=True)
class OnboardingSnapshot:
    persona: OnboardingPersona
    steps: list[OnboardingStep]
    completed_steps: int
    total_steps: int
    progress_percent: int
    next_step: Optional[OnboardingStepKey]


def resolve_persona(db: Session, user: User) -> OnboardingPersona:
    if get_staff_record(db, user):
        return "staff"
    if user.role == "business_owner":
        return "owner"
    if user.role == "specialist":
        return "solo"
    # platform admins and former staff never get owner actions in the checklist
    return "staff"


def _active_employee_ids(db: Session, owner_id: int) -> list[int]:
    rows = (
        db.query(Employee.employee_user_id)
        .join(User, Employee.employee_user_id == User.id)
        .filter(
            Employee.business_owner_id == owner_id,
            Employee.is_active.is_(True),
            User.deleted_at.is_(None),
        )
        .all()
    )
    return [row[0] for row in rows]


def _has_active_service(
    db: Session,
    user: User,
    business_id: Optional[int],
    staff_ids: list[int],
) -> bool:
    scope = [Service.specialist_id == user.id, Service.specialist_id.in_(staff_ids)]
    if business_id:
        scope.append(Service.business_id == business_id)
    return (
        db.query(Service.id)
        .filter(
            Service.is_active.is_(True),
            Service.deleted_at.is_(None),
            Service.archived_at.is_(None),
            or_(*scope),
        )
        .first()
        is not None
    )


def _owner_works_as_master(db: Session, user: User, business_id: Optional[int]) -> bool:
    query = db.query(Service.id).filter(
        Service.specialist_id == user.id,
        Service.is_active.is_(True),
        Service.deleted_at.is_(None),
        Service.archived_at.is_(None),
    )
    if business_id:
        query = query.filter(Service.business_id == business_id)
    return query.first() is not None


def _has_schedule(db: Session, specialist_ids: list[int]) -> bool:
    if not specialist_ids:
        return False
    return db.query(WorkSchedule.id).filter(
        WorkSchedule.specialist_id.in_(specialist_ids),
    ).first() is not None


def _booking_enabled(db: Session, business_id: Optional[int]) -> bool:
    if not business_id:
        return False
    return (
        db.query(PublicBookingSettings.id)
        .filter(
            PublicBookingSettings.business_id == business_id,
            PublicBookingSettings.is_enabled.is_(True),
        )
        .first()
        is not None
    )


def _step(key: OnboardingStepKey, completed: bool, available: bool) -> OnboardingStep:
    return OnboardingStep(
        key=key,
        completed=completed,
        available=available,
        required=available,
    )


def compute_onboarding(db: Session, user: User) -> OnboardingSnapshot:
    persona = resolve_persona(db, user)
    if persona == "staff":
        return OnboardingSnapshot(persona, [], 0, 0, 0, None)

    business_id = get_user_business_id(user, db) if persona == "owner" else user.business_id
    own_staff_ids = _active_employee_ids(db, user.id) if persona == "owner" else []
    steps: list[OnboardingStep] = []

    if persona == "owner":
        business_done = bool(
            business_id
            and db.query(Business.id).filter(
                Business.id == business_id,
                Business.deleted_at.is_(None),
            ).first()
        )
        steps.append(_step("business", business_done, True))
        service_done = business_done and _has_active_service(db, user, business_id, own_staff_ids)
        steps.append(_step("service", service_done, business_done))
        master_ids = [user.id, *own_staff_ids]
        staff_done = business_done and (
            bool(own_staff_ids) or _owner_works_as_master(db, user, business_id)
        )
        steps.append(_step("staff", staff_done, business_done))
    else:
        business_done = False
        service_done = _has_active_service(db, user, None, [])
        steps.append(_step("service", service_done, True))
        staff_done = True
        master_ids = [user.id]
        steps.append(_step("staff", True, True))

    schedule_done = staff_done and _has_schedule(db, master_ids)
    steps.append(_step("schedule", schedule_done, SCHEDULE_STEP_READY and staff_done))
    # a solo specialist without a business has no public link to turn on
    booking_done = business_done and _booking_enabled(db, business_id)
    steps.append(_step("booking", booking_done, BOOKING_STEP_READY and schedule_done and business_done))

    required = [step for step in steps if step.required]
    done = [step for step in required if step.completed]
    percent = round(len(done) * 100 / len(required)) if required else 0
    next_step = next((step.key for step in required if not step.completed), None)
    return OnboardingSnapshot(
        persona=persona,
        steps=steps,
        completed_steps=len(done),
        total_steps=len(required),
        progress_percent=percent,
        next_step=next_step,
    )
