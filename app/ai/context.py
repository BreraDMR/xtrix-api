"""The little the model needs to know (А5): time, the user's specialists and services, and which of
the user's own clients the request might mean. Ids and names only - no phones, e-mails, birth dates,
notes or anything medical, ever. Everything here is read through the same permission helpers as the API.
"""
import re
from datetime import datetime, time, timedelta, timezone
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
    check_visit_access,
    get_staff_record,
    get_user_business_id,
)
from app.api.crud import crud_business
from app.db.models.employee import Employee
from app.db.models.service import Service
from app.db.models.user import User
from app.db.models.visit import Visit

MAX_SPECIALISTS = 30
MAX_SERVICES = 60
MAX_CLIENT_MATCHES = 10
# a matched client's visits, so "move Anna's haircut" has an id to point at
VISITS_BACK = timedelta(days=7)
VISITS_AHEAD = timedelta(days=60)
MAX_VISITS_PER_CLIENT = 8
CALENDAR_DAYS = 15  # today and two weeks ahead - "in two weeks" has to land inside it


def calendar(local_now: datetime, zone) -> list[str]:
    """"2026-10-27 Tue +01:00" per day. Models miscount weekdays and miss the clock change on
    their own, so we spell both out; the offset is the one at noon that day."""
    out = []
    for n in range(CALENDAR_DAYS):
        day = local_now.date() + timedelta(days=n)
        noon = datetime.combine(day, time(12), tzinfo=zone)
        line = f"{day.isoformat()} {noon.strftime('%a')} {noon.strftime('%z')[:3]}:{noon.strftime('%z')[3:]}"
        out.append(line + {0: " today", 1: " tomorrow"}.get(n, ""))
    return out


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


def _with_visits(db: Session, user: User, matches: list[dict], local_now: datetime, zone) -> list[dict]:
    """Each matched client gets their live visits from a week back to two months ahead: id, salon
    time, service, master, status. No notes or prices - notes can hold anything."""
    ids = [m["id"] for m in matches]
    if not ids:
        return matches
    now = local_now.astimezone(timezone.utc).replace(tzinfo=None)
    rows = db.query(Visit).filter(Visit.client_id.in_(ids), Visit.deleted_at.is_(None),
                                  or_(Visit.status.is_(None), Visit.status != "cancelled"),
                                  Visit.date_start >= now - VISITS_BACK, Visit.date_start <= now + VISITS_AHEAD
                                  ).order_by(Visit.date_start, Visit.id).limit(MAX_VISITS_PER_CLIENT * len(ids) * 2).all()
    names = {}
    by_client: dict[int, list] = {}
    for v in rows:
        kept = by_client.setdefault(v.client_id, [])
        if len(kept) >= MAX_VISITS_PER_CLIENT or not check_visit_access(db, user, v):
            continue
        if v.specialist_id not in names:
            master = db.get(User, v.specialist_id)
            names[v.specialist_id] = master.full_name if master else None
        kept.append({"id": v.id, "start": v.date_start.replace(tzinfo=timezone.utc).astimezone(zone).isoformat(),
                     "service": v.service_name, "specialist_id": v.specialist_id,
                     "specialist": names[v.specialist_id], "status": v.status or "planned"})
    return [{**m, "visits": by_client.get(m["id"], [])} for m in matches]


# a visit number the person typed: "визит №309", "návštěva č. 309", "visit #309", "№309"
_VISIT_NUMBER = re.compile(r"(?:№|#|\bč\.|\bc\.|\bno\.|номер|визит\w*|návštěv\w*|navstev\w*|"
                           r"rezervac\w*|\bvisit\w*|\bbooking|\bappointment)\s*(?:№|#|č\.|no\.|номер)?\s*(\d{1,9})\b",
                           re.I)
MAX_MENTIONED_VISITS = 5


def _mentioned_visits(db: Session, user: User, text: str, zone) -> list[dict]:
    """Visits whose number is in the request, if they're the user's own - else simply nothing."""
    from app.db.models.client import Client

    ids = list(dict.fromkeys(int(n) for n in _VISIT_NUMBER.findall(text or "")))[:MAX_MENTIONED_VISITS]
    if not ids:
        return []
    out = []
    for v in db.query(Visit).filter(Visit.id.in_(ids), Visit.deleted_at.is_(None)).order_by(Visit.id).all():
        if not check_visit_access(db, user, v):
            continue
        client = db.get(Client, v.client_id) if v.client_id else None
        master = db.get(User, v.specialist_id) if v.specialist_id else None
        out.append({"id": v.id, "client_id": v.client_id, "client": client.full_name if client else None,
                    "start": v.date_start.replace(tzinfo=timezone.utc).astimezone(zone).isoformat(),
                    "service": v.service_name, "specialist_id": v.specialist_id,
                    "specialist": master.full_name if master else None, "status": v.status or "planned"})
    return out


def _matches(db: Session, user: User, text: str, history: Optional[list]) -> list[dict]:
    """Clients named in the request; with nobody named ("перенеси её на пятницу"), the ones named
    in the latest message of the conversation that names somebody."""
    found = clients_in_text(db, user, text, limit=MAX_CLIENT_MATCHES)
    for item in reversed(history or []):
        if found:
            break
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            found = clients_in_text(db, user, item["text"], limit=MAX_CLIENT_MATCHES)
    return found


def build_context(db: Session, user: User, text: str, *, locale: str, tz_name: str,
                  now: Optional[datetime] = None, history: Optional[list] = None) -> dict:
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
        "calendar": calendar(local_now, zone),
        "timezone": tz_name,
        "language": locale,
        "assistant": {"name": persona.name, "gender": persona.gender},
        "current_user": {"id": user.id, "name": user.full_name},
        "specialists": specialists,
        "services": _services(db, user, business_id, [s["id"] for s in specialists]),
        "client_matches": _with_visits(db, user, _matches(db, user, text, history), local_now, zone),
        "mentioned_visits": _mentioned_visits(db, user, text, zone),
        "history": list(history or []),
    }
