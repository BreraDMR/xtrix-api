"""Runs a confirmed AssistantPlan as the current user (А2).

This is the real guard against a model that was talked into something: every write goes through
the same endpoint functions the HTTP API uses, with the same role, permission and tenant checks,
so another business's id fails here exactly like it fails over HTTP. Reads that the endpoints
don't offer in a usable shape (client lookup, free-slot search) are built on the same
permission helpers. The model never touches the database.

Actions run in order and the run stops at the first failure; the result says what was done.
"""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal, Optional
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.ai import client_search
from app.ai import plan as p
from app.api.core.clock import utcnow
from app.api.core.logger import get_logger
from app.api.core.permissions import (
    check_service_access,
    check_specialist_in_scope,
)
from app.api.core.slots import find_next_available_slot
from app.api.crud import crud_schedule
from app.db.models.service import Service
from app.db.models.user import User
from app.db.models.visit import Visit
from app.schemas.client import ClientCreate, ClientUpdate
from app.schemas.datetimes import to_naive_utc, utc_iso
from app.schemas.visit import VisitCreate, VisitUpdate

logger = get_logger("ai.executor")

# same roles the client and visit endpoints let in
ALLOWED_ROLES = ("admin", "business_owner", "specialist", "employee")
DEFAULT_MINUTES = 60
SLOT_STEP_MINUTES = 15
MAX_NOTE = 5000
CLIENT_FIELDS = ("full_name", "birth_date", "gender", "contact_phone", "contact_email", "language")


@dataclass
class ActionResult:
    index: int
    action: str
    status: Literal["done", "failed", "skipped"]
    data: dict = field(default_factory=dict)
    error: Optional[dict] = None


@dataclass
class ExecutionResult:
    results: list

    @property
    def ok(self) -> bool:
        return all(r.status == "done" for r in self.results)

    @property
    def done_count(self) -> int:
        return sum(r.status == "done" for r in self.results)

    @property
    def failed_index(self) -> Optional[int]:
        return next((r.index for r in self.results if r.status == "failed"), None)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "done_count": self.done_count, "failed_index": self.failed_index,
                "results": [asdict(r) for r in self.results]}


class ActionFailed(Exception):
    def __init__(self, status: int, code: str, message: str = "", data: Optional[dict] = None):
        super().__init__(code)
        self.status, self.code, self.message, self.data = status, code, message or code, data or {}


_CODES = {400: "bad_request", 403: "not_allowed", 404: "not_found", 409: "conflict", 422: "invalid"}


def _from_http(exc: HTTPException) -> ActionFailed:
    detail = exc.detail
    data = {}
    if isinstance(detail, dict):
        message = str(detail.get("message") or "")
        code = str(detail.get("code") or _CODES.get(exc.status_code, "error"))
        if "existing_client_id" in detail:
            data["existing_client_id"] = detail["existing_client_id"]
    else:
        message, code = str(detail or ""), _CODES.get(exc.status_code, "error")
    return ActionFailed(exc.status_code, code, message, data)


def _from_validation(exc: ValidationError) -> ActionFailed:
    first = exc.errors()[0] if exc.errors() else {}
    where = ".".join(str(x) for x in first.get("loc", ()))
    # the error type and field, not the rejected value
    return ActionFailed(422, "invalid", f"{where}: {first.get('type', 'invalid')}")


def _get(obj: Any, name: str):
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def _iso(dt: datetime) -> str:
    return utc_iso(dt)


def _parse_iso(value: str) -> datetime:
    return to_naive_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _internal_request() -> Request:
    """Endpoint deps that only log a correlation id still need a Request when called in-process."""
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/assistant/execute",
            "raw_path": b"/assistant/execute",
            "query_string": b"",
            "headers": [],
            "client": ("assistant", 0),
            "server": ("assistant", 80),
        }
    )


class _Run:
    def __init__(self, db: Session, user: User, locale: str, tz: ZoneInfo, now: datetime):
        self.db, self.user, self.locale, self.tz, self.now = db, user, locale, tz, now
        self.refs: dict[str, dict] = {}

    # --- helpers -------------------------------------------------------------
    def ep(self, *, request: bool = False) -> dict:
        values = dict(db=self.db, current_user=self.user, locale=self.locale)
        if request:
            values["request"] = _internal_request()
        return values

    def client_id(self, action) -> int:
        if action.client_id is not None:
            return action.client_id
        return self.refs[action.client_ref]["client_id"]

    def slot(self, ref: str) -> dict:
        return self.refs[ref]

    def own_service(self, service_id: Optional[int]) -> Optional[Service]:
        if not service_id:
            return None
        service = self.db.query(Service).filter(Service.id == service_id).first()
        if not service or not check_service_access(self.db, self.user, service):
            raise ActionFailed(403, "not_allowed")
        return service

    def to_local(self, utc_naive: datetime) -> datetime:
        return utc_naive.replace(tzinfo=timezone.utc).astimezone(self.tz).replace(tzinfo=None)

    def to_utc(self, local_naive: datetime) -> datetime:
        return local_naive.replace(tzinfo=self.tz).astimezone(timezone.utc).replace(tzinfo=None)

    def get_visit(self, visit_id: int):
        from app.api.v1.endpoints import visits as visits_ep
        return visits_ep.get_visit(visit_id=visit_id, include_deleted=False, **self.ep())

    def update_visit(self, visit_id: int, payload: VisitUpdate):
        from app.api.v1.endpoints import visits as visits_ep
        return visits_ep.update_visit(visit_id=visit_id, payload=payload, **self.ep(request=True))

    # --- actions -------------------------------------------------------------
    def find_client(self, a: p.FindClient) -> dict:
        rows = client_search.find_clients(self.db, self.user, name=a.name, phone=a.phone, email=a.email)
        if not rows:
            raise ActionFailed(404, "client_not_found")
        if len(rows) > 1:
            raise ActionFailed(409, "ambiguous", "more than one client matches",
                               {"candidates": [{"client_id": cid, "full_name": name} for cid, name in rows[:5]]})
        return {"client_id": rows[0][0], "full_name": rows[0][1]}

    def create_client(self, a: p.CreateClient) -> dict:
        from app.api.v1.endpoints import clients as clients_ep
        fields = {f: getattr(a, f) for f in CLIENT_FIELDS if getattr(a, f) is not None}
        created = clients_ep.create_client(client=ClientCreate(**fields), **self.ep())
        return {"client_id": _get(created, "id"), "full_name": _get(created, "full_name")}

    def update_client(self, a: p.UpdateClient) -> dict:
        from app.api.v1.endpoints import clients as clients_ep
        client_id = self.client_id(a)
        fields = {f: getattr(a, f) for f in CLIENT_FIELDS if getattr(a, f) is not None}
        clients_ep.update_client(client_id=client_id, payload=ClientUpdate(**fields), **self.ep(request=True))
        return {"client_id": client_id, "changed": sorted(fields)}

    def find_free_slot(self, a: p.FindFreeSlot) -> dict:
        specialist_id = a.specialist_id or self.user.id
        # who you may book for, you may look up free time for
        if not check_specialist_in_scope(self.db, self.user, specialist_id):
            raise ActionFailed(403, "not_allowed")
        service = self.own_service(a.service_id)
        minutes = a.duration_minutes or (service.duration_minutes if service and service.duration_minutes
                                         else DEFAULT_MINUTES)
        start_utc = max(self.now, to_naive_utc(a.not_before)) if a.not_before else self.now
        horizon = start_utc + timedelta(days=a.days_ahead + 1)
        busy = self.db.query(Visit).filter(
            Visit.specialist_id == specialist_id,
            Visit.deleted_at.is_(None),
            Visit.status != "cancelled",
            Visit.date_start < horizon,
            Visit.date_start > start_utc - timedelta(days=1),
        ).all()
        existing = [(self.to_local(v.date_start),
                     self.to_local(v.date_end or v.date_start + timedelta(hours=1))) for v in busy]
        blocks = [(self.to_local(b.start), self.to_local(b.end))
                  for b in crud_schedule.list_block_slots(self.db, specialist_id)]
        schedule = [(s.weekday, s.start_time, s.end_time)
                    for s in crud_schedule.list_work_schedules(self.db, specialist_id)]
        exceptions = [(e.exception_date, e.start_time, e.end_time)
                      for e in crud_schedule.list_schedule_exceptions(self.db, specialist_id)]
        local_start = self.to_local(start_utc).replace(second=0, microsecond=0)
        overshoot = local_start.minute % SLOT_STEP_MINUTES
        if overshoot:
            local_start += timedelta(minutes=SLOT_STEP_MINUTES - overshoot)
        found = find_next_available_slot(existing, minutes, start_from=local_start, days_ahead=a.days_ahead,
                                         step_minutes=SLOT_STEP_MINUTES, schedule=schedule or None,
                                         blocks=blocks or None, exceptions=exceptions or None)
        if not found:
            raise ActionFailed(404, "no_slot")
        start = self.to_utc(found)
        return {"start": _iso(start), "end": _iso(start + timedelta(minutes=minutes)),
                "specialist_id": specialist_id, "service_id": service.id if service else None,
                "duration_minutes": minutes}

    def create_visit(self, a: p.CreateVisit) -> dict:
        from app.api.v1.endpoints import visits as visits_ep
        client_id = self.client_id(a)
        slot = self.slot(a.slot_ref) if a.slot_ref else None
        service_id = a.service_id or (slot or {}).get("service_id")
        specialist_id = a.specialist_id or (slot or {}).get("specialist_id") or self.user.id
        if slot and specialist_id != slot["specialist_id"]:
            raise ActionFailed(409, "slot_for_other_specialist")
        start = _parse_iso(slot["start"]) if slot else to_naive_utc(a.start)
        service = self.own_service(service_id)
        minutes = a.duration_minutes or (slot or {}).get("duration_minutes") or (
            service.duration_minutes if service and service.duration_minutes else DEFAULT_MINUTES)
        visit = VisitCreate(
            client_id=client_id, specialist_id=specialist_id, service_id=service_id,
            date_start=start, date_end=start + timedelta(minutes=minutes),
            service_name=service.name if service else None,
            price=service.price if service and service.price is not None else None,
            note=a.note, status="planned",
        )
        created = visits_ep.create_visit(visit=visit, **self.ep(request=True))
        return {"visit_id": _get(created, "id"), "client_id": client_id, "specialist_id": specialist_id,
                "start": _iso(start), "end": _iso(start + timedelta(minutes=minutes))}

    def reschedule_visit(self, a: p.RescheduleVisit) -> dict:
        visit = self.get_visit(a.visit_id)
        old_start, old_end = _get(visit, "date_start"), _get(visit, "date_end")
        slot = self.slot(a.slot_ref) if a.slot_ref else None
        if slot and slot["specialist_id"] != _get(visit, "specialist_id"):
            raise ActionFailed(409, "slot_for_other_specialist")
        start = _parse_iso(slot["start"]) if slot else to_naive_utc(a.start)
        length = (old_end - old_start) if old_end else timedelta(minutes=DEFAULT_MINUTES)
        self.update_visit(a.visit_id, VisitUpdate(date_start=start, date_end=start + length))
        return {"visit_id": a.visit_id, "start": _iso(start), "end": _iso(start + length)}

    def cancel_visit(self, a: p.CancelVisit) -> dict:
        self.update_visit(a.visit_id, VisitUpdate(status="cancelled"))
        return {"visit_id": a.visit_id, "status": "cancelled"}

    def add_note(self, a: p.AddNote) -> dict:
        visit = self.get_visit(a.visit_id)
        old = _get(visit, "note") or ""
        note = f"{old}\n{a.text}" if old else a.text
        if len(note) > MAX_NOTE:
            raise ActionFailed(422, "note_too_long")
        self.update_visit(a.visit_id, VisitUpdate(note=note))
        return {"visit_id": a.visit_id}


HANDLERS: dict[str, Callable] = {
    "find_client": _Run.find_client,
    "create_client": _Run.create_client,
    "update_client": _Run.update_client,
    "find_free_slot": _Run.find_free_slot,
    "create_visit": _Run.create_visit,
    "reschedule_visit": _Run.reschedule_visit,
    "cancel_visit": _Run.cancel_visit,
    "add_note": _Run.add_note,
}


def execute_plan(db: Session, user: User, plan: p.AssistantPlan, *, locale: str = "en",
                 tz: str = "Europe/Prague", now: Optional[datetime] = None) -> ExecutionResult:
    try:
        zone = ZoneInfo(tz)
    except Exception:
        zone = ZoneInfo("UTC")
    run = _Run(db, user, locale, zone, now or utcnow())
    results: list[ActionResult] = []
    stopped = False
    for i, action in enumerate(plan.actions):
        if stopped:
            results.append(ActionResult(i, action.action, "skipped"))
            continue
        try:
            if user.role not in ALLOWED_ROLES:
                raise ActionFailed(403, "not_allowed")
            data = HANDLERS[action.action](run, action)
            if getattr(action, "ref", None):
                run.refs[action.ref] = data
            results.append(ActionResult(i, action.action, "done", data))
        except Exception as exc:
            db.rollback()
            if isinstance(exc, ActionFailed):
                failed = exc
            elif isinstance(exc, HTTPException):
                failed = _from_http(exc)
            elif isinstance(exc, ValidationError):
                failed = _from_validation(exc)
            elif isinstance(exc, IntegrityError):
                # e.g. the no-double-booking constraint when two requests race
                failed = ActionFailed(409, "conflict")
            elif isinstance(exc, ValueError):
                failed = ActionFailed(400, "bad_request", str(exc)[:300])
            else:
                logger.error("ai action crashed: user=%s action=%s error=%s", user.id, action.action,
                             type(exc).__name__)
                failed = ActionFailed(500, "error")
            results.append(ActionResult(i, action.action, "failed", failed.data,
                                        {"status": failed.status, "code": failed.code, "message": failed.message}))
            stopped = True
        logger.info("ai action: user=%s index=%s action=%s status=%s", user.id, i, action.action,
                    results[-1].status)
    return ExecutionResult(results)
