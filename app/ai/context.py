"""The little the model needs to know (А5): time, the user's specialists and services, and which of
the user's own clients the request might mean. Ids and names only - no phones, e-mails, birth dates,
notes or anything medical, ever. Everything here is read through the same permission helpers as the API.
"""
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.ai.client_search import clients_in_text
from app.ai.persona import effective_assistant
from app.api.core.clock import utcnow
from app.api.core.permissions import (
    check_service_access,
    check_specialist_in_scope,
    get_staff_record,
    get_user_business_id,
)
from app.api.crud import crud_business
from app.db.models.employee import Employee
from app.db.models.service import Service
from app.db.models.user import User

MAX_SPECIALISTS = 30
MAX_SERVICES = 60
MAX_CLIENT_MATCHES = 10


def _specialists(db: Session, user: User, business_id: Optional[int]) -> list[dict]:
    candidates = {user.id: user}
    if business_id:
        for u in db.query(User).filter(User.business_id == business_id, User.deleted_at.is_(None)).limit(100):
            candidates.setdefault(u.id, u)
    staff = get_staff_record(db, user)
    owner_id = staff.business_owner_id if staff else user.id
    employees = db.query(User).join(Employee, Employee.employee_user_id == User.id).filter(
        Employee.business_owner_id == owner_id, Employee.is_active.is_(True), User.deleted_at.is_(None)).limit(100)
    for u in employees:
        candidates.setdefault(u.id, u)
    out = [{"id": u.id, "name": u.full_name} for u in candidates.values()
           if check_specialist_in_scope(db, user, u.id)]
    return sorted(out, key=lambda s: s["id"])[:MAX_SPECIALISTS]


def _services(db: Session, user: User, business_id: Optional[int], specialist_ids: list[int]) -> list[dict]:
    q = db.query(Service).filter(Service.deleted_at.is_(None), Service.archived_at.is_(None),
                                 Service.is_active.isnot(False))
    scope = [Service.specialist_id.in_(specialist_ids)] if specialist_ids else []
    if business_id:
        scope.append(Service.business_id == business_id)
    if not scope:
        return []
    rows = q.filter(or_(*scope)).order_by(Service.id).limit(MAX_SERVICES * 2).all()
    return [{"id": s.id, "name": s.name, "duration_minutes": s.duration_minutes, "specialist_id": s.specialist_id}
            for s in rows if check_service_access(db, user, s)][:MAX_SERVICES]


def build_context(db: Session, user: User, text: str, *, locale: str, tz_name: str,
                  now: Optional[datetime] = None) -> dict:
    try:
        zone = ZoneInfo(tz_name)
    except Exception:
        zone, tz_name = ZoneInfo("UTC"), "UTC"
    local_now = (now or utcnow()).replace(tzinfo=timezone.utc).astimezone(zone).replace(microsecond=0)
    business_id = get_user_business_id(user, db)
    business = crud_business.get_business(db, business_id) if business_id else None
    persona = effective_assistant(business, locale)
    specialists = _specialists(db, user, business_id)
    return {
        "now": local_now.isoformat(),
        "weekday": local_now.strftime("%A"),
        "timezone": tz_name,
        "language": locale,
        "assistant": {"name": persona.name, "gender": persona.gender},
        "current_user": {"id": user.id, "name": user.full_name},
        "specialists": specialists,
        "services": _services(db, user, business_id, [s["id"] for s in specialists]),
        "client_matches": clients_in_text(db, user, text, limit=MAX_CLIENT_MATCHES),
    }
