"""What each planned action will touch, in words the user can check before pressing "confirm".

Resolved by our code at plan time, read-only, through the same permission helpers as the API and the
executor: an id from another business (or one that doesn't exist) resolves to nothing, and the executor
refuses it again later anyway. Names, times and the note only - never phones, e-mails, birth dates or
anything medical.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.ai import plan as p
from app.ai.executor import DEFAULT_MINUTES, ActionFailed, _parse_iso, _Run
from app.api.core.clock import utcnow
from app.api.core.logger import get_logger
from app.api.core.permissions import (
    check_service_access,
    check_specialist_in_scope,
    check_visit_access,
    get_accessible_client_ids,
)
from app.db.models.client import Client
from app.db.models.service import Service
from app.db.models.user import User
from app.db.models.visit import Visit
from app.schemas.datetimes import to_naive_utc

logger = get_logger("ai.display")


@dataclass
class ActionDisplay:
    client_name: Optional[str] = None
    service_name: Optional[str] = None
    specialist_name: Optional[str] = None
    start_time: Optional[datetime] = None  # naive UTC, like everything in the database
    end_time: Optional[datetime] = None
    note: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


class _Describer:
    def __init__(self, db: Session, user: User, tz: ZoneInfo, now: datetime):
        self.db, self.user = db, user
        # the executor's own read-only lookups (client search, free-slot search), so the preview
        # finds exactly what the run would find right now
        self.run = _Run(db, user, "en", tz, now)
        self._client_ids: Optional[set] = None
        self.refs: dict[str, dict] = {}

    # --- lookups, each one empty for anything outside the user's scope -------
    def client_name(self, client_id: Optional[int]) -> Optional[str]:
        if not client_id:
            return None
        if self._client_ids is None:
            self._client_ids = get_accessible_client_ids(self.db, self.user) or set()
        if client_id not in self._client_ids:
            return None
        client = self.db.query(Client).filter(Client.id == client_id, Client.deleted_at.is_(None)).first()
        return client.full_name if client else None

    def specialist_name(self, specialist_id: Optional[int]) -> Optional[str]:
        if not specialist_id or not check_specialist_in_scope(self.db, self.user, specialist_id):
            return None
        u = self.db.query(User).filter(User.id == specialist_id, User.deleted_at.is_(None)).first()
        return u.full_name if u else None

    def service(self, service_id: Optional[int]) -> Optional[Service]:
        if not service_id:
            return None
        s = self.db.query(Service).filter(Service.id == service_id, Service.deleted_at.is_(None)).first()
        return s if s and check_service_access(self.db, self.user, s) else None

    def visit(self, visit_id: int) -> Optional[Visit]:
        v = self.db.query(Visit).filter(Visit.id == visit_id, Visit.deleted_at.is_(None)).first()
        return v if v and check_visit_access(self.db, self.user, v) else None

    def client_of(self, a) -> Optional[str]:
        if getattr(a, "client_ref", None):
            return self.refs.get(a.client_ref, {}).get("client_name")
        return self.client_name(getattr(a, "client_id", None))

    def visit_display(self, v: Visit, **override) -> ActionDisplay:
        service = self.service(v.service_id)
        d = ActionDisplay(client_name=self.client_name(v.client_id),
                          service_name=service.name if service else v.service_name,
                          specialist_name=self.specialist_name(v.specialist_id),
                          start_time=v.date_start, end_time=v.date_end)
        for k, val in override.items():
            setattr(d, k, val)
        return d

    # --- actions -------------------------------------------------------------
    def find_client(self, a: p.FindClient) -> ActionDisplay:
        try:
            found = self.run.find_client(a)
        except ActionFailed:
            return ActionDisplay()
        if a.ref:
            self.refs[a.ref] = {"client_name": found["full_name"]}
        return ActionDisplay(client_name=found["full_name"])

    def create_client(self, a: p.CreateClient) -> ActionDisplay:
        if a.ref:
            self.refs[a.ref] = {"client_name": a.full_name}
        return ActionDisplay(client_name=a.full_name)

    def update_client(self, a: p.UpdateClient) -> ActionDisplay:
        return ActionDisplay(client_name=self.client_of(a))

    def find_free_slot(self, a: p.FindFreeSlot) -> ActionDisplay:
        service = self.service(a.service_id)
        d = ActionDisplay(specialist_name=self.specialist_name(a.specialist_id or self.user.id),
                          service_name=service.name if service else None)
        try:
            slot = self.run.find_free_slot(a)
        except ActionFailed:
            return d
        if a.ref:
            self.refs[a.ref] = slot
        d.start_time, d.end_time = _parse_iso(slot["start"]), _parse_iso(slot["end"])
        return d

    def create_visit(self, a: p.CreateVisit) -> ActionDisplay:
        slot = self.refs.get(a.slot_ref, {}) if a.slot_ref else {}
        service = self.service(a.service_id or slot.get("service_id"))
        start = _parse_iso(slot["start"]) if slot.get("start") else (to_naive_utc(a.start) if a.start else None)
        minutes = a.duration_minutes or slot.get("duration_minutes") or (
            service.duration_minutes if service and service.duration_minutes else DEFAULT_MINUTES)
        return ActionDisplay(
            client_name=self.client_of(a),
            service_name=service.name if service else None,
            specialist_name=self.specialist_name(a.specialist_id or slot.get("specialist_id") or self.user.id),
            start_time=start,
            end_time=start + timedelta(minutes=minutes) if start else None,
            note=a.note,
        )

    def reschedule_visit(self, a: p.RescheduleVisit) -> ActionDisplay:
        v = self.visit(a.visit_id)
        if not v:
            return ActionDisplay()
        slot = self.refs.get(a.slot_ref, {}) if a.slot_ref else {}
        start = _parse_iso(slot["start"]) if slot.get("start") else (to_naive_utc(a.start) if a.start else None)
        length = (v.date_end - v.date_start) if v.date_end else timedelta(minutes=DEFAULT_MINUTES)
        return self.visit_display(v, start_time=start, end_time=start + length if start else None)

    def cancel_visit(self, a: p.CancelVisit) -> ActionDisplay:
        v = self.visit(a.visit_id)
        return self.visit_display(v) if v else ActionDisplay()

    def add_note(self, a: p.AddNote) -> ActionDisplay:
        v = self.visit(a.visit_id)
        return self.visit_display(v, note=a.text) if v else ActionDisplay()


def describe_plan(db: Session, user: User, plan: p.AssistantPlan, *, tz: str,
                  now: Optional[datetime] = None) -> list[ActionDisplay]:
    try:
        zone = ZoneInfo(tz)
    except Exception:
        zone = ZoneInfo("UTC")
    d = _Describer(db, user, zone, now or utcnow())
    out = []
    for i, action in enumerate(plan.actions):
        try:
            out.append(getattr(d, action.action)(action))
        except Exception as exc:
            # a preview must never break the plan answer - show nothing for this one
            db.rollback()
            logger.warning("ai display failed: user=%s index=%s error=%s", user.id, i, type(exc).__name__)
            out.append(ActionDisplay())
    return out
