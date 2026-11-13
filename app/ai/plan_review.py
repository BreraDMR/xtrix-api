"""A look at the model's plan before the person sees it (duty plan item 8).

The executor is still the real guard - this only stops us from showing steps that can't work or
that are obviously off, and turns them into a plain question instead:

- ids that aren't in the user's own salon (or don't exist) - the step is dropped, and so is every
  step built on its ref. Usually that's a prompt injection or a made-up id.
- a time with the wrong summer/winter offset for that date ("10:00+02:00" on 27.10 in Prague):
  the person said 10:00, so we keep the wall clock and take the salon's offset for that day.
  A plain UTC time (or any offset the salon never uses) is a real instant and stays as it is.
- a booking or reschedule that's already in the past, beyond a short grace - dropped with a question.
- a plan with nothing to do whose summary is a question: the summary goes into questions too.

Reads only, through the same permission helpers as the API.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.ai.plan import MAX_QUESTIONS, AssistantPlan
from app.api.core.permissions import (
    check_client_access,
    check_service_access,
    check_specialist_in_scope,
    check_visit_access,
)
from app.db.models.service import Service
from app.db.models.user import User
from app.db.models.visit import Visit

# a visit that started a little while ago is the client in the chair, not a typo
PAST_GRACE = timedelta(minutes=30)
WHEN_FIELDS = ("start", "not_before")
BOOKINGS = ("create_visit", "reschedule_visit")
SUMMARY_MAX = 500
QUESTION_MAX = 300

_TEXT = {
    "foreign": {
        "note": {
            "en": "Part of the request points at records outside your salon, so that step is not in the plan.",
            "ru": "Часть запроса относится к записям не из вашего салона, поэтому этого шага нет в плане.",
            "cz": "Část požadavku míří na záznamy mimo váš salon, proto tento krok v plánu není.",
        },
        "question": {
            "en": "Which client or visit did you mean? A name and date help me find it.",
            "ru": "Уточните, какого клиента или какой визит вы имели в виду? Подойдут имя и дата.",
            "cz": "Upřesněte, kterého klienta nebo kterou návštěvu jste měli na mysli? Stačí jméno a datum.",
        },
    },
    "past": {
        "note": {
            "en": "The requested time has already passed, so the booking is not in the plan.",
            "ru": "Указанное время уже прошло, поэтому записи нет в плане.",
            "cz": "Požadovaný čas už uplynul, proto rezervace v plánu není.",
        },
        "question": {
            "en": "That time has already passed. Which day and time should I use instead?",
            "ru": "Это время уже прошло. На какой день и время записать?",
            "cz": "Tento čas už uplynul. Na jaký den a čas to mám zapsat?",
        },
    },
}


def _say(kind: str, part: str, locale: str) -> str:
    texts = _TEXT[kind][part]
    return texts.get(locale, texts["en"])


def _salon_offsets(zone: ZoneInfo, year: int) -> set:
    return {datetime(year, month, 15, 12, tzinfo=zone).utcoffset() for month in (1, 7)}


def fix_offset(value: Optional[datetime], zone: ZoneInfo) -> Optional[datetime]:
    """Same wall clock with the salon's offset for that date, when the model used the salon's
    other (summer/winter) offset. Anything else is left alone."""
    if value is None or value.utcoffset() is None:
        return value
    wall = value.replace(tzinfo=None)
    right = wall.replace(tzinfo=zone)
    if value.utcoffset() == right.utcoffset():
        return value
    if value.utcoffset() not in _salon_offsets(zone, wall.year):
        return value
    return right.astimezone(timezone(right.utcoffset()))


class _Scope:
    """Cached yes/no per id: can this user act on it?"""

    def __init__(self, db: Session, user: User):
        self.db, self.user = db, user
        self.seen: dict[tuple, bool] = {}

    def ok(self, field: str, value: int) -> bool:
        key = (field, value)
        if key not in self.seen:
            self.seen[key] = self._check(field, value)
        return self.seen[key]

    def _check(self, field: str, value: int) -> bool:
        db, user = self.db, self.user
        if field == "client_id":
            return check_client_access(db, user, value)
        if field == "specialist_id":
            return check_specialist_in_scope(db, user, value)
        if field == "service_id":
            service = db.query(Service).filter(Service.id == value, Service.deleted_at.is_(None)).first()
            return bool(service) and check_service_access(db, user, service)
        if field == "visit_id":
            visit = db.query(Visit).filter(Visit.id == value, Visit.deleted_at.is_(None)).first()
            return bool(visit) and check_visit_access(db, user, visit)
        return True


ID_FIELDS = ("client_id", "specialist_id", "service_id", "visit_id")


def review_plan(db: Session, user: User, plan: AssistantPlan, *, locale: str, tz: str,
                now: Optional[datetime] = None) -> AssistantPlan:
    """now: naive UTC, like everywhere else in the assistant."""
    try:
        zone = ZoneInfo(tz)
    except Exception:
        zone = ZoneInfo("UTC")
    now_utc = (now or datetime.now(timezone.utc).replace(tzinfo=None)).replace(tzinfo=timezone.utc)
    scope = _Scope(db, user)
    kept, dropped_refs, why = [], set(), []

    for action in plan.actions:
        problem = None
        for field in ID_FIELDS:
            value = getattr(action, field, None)
            if value is not None and not scope.ok(field, value):
                problem = "foreign"
        for field in ("client_ref", "slot_ref"):
            if getattr(action, field, None) in dropped_refs:
                problem = problem or "foreign"
        fixed = {f: fix_offset(getattr(action, f), zone) for f in WHEN_FIELDS
                 if getattr(action, f, None) is not None}
        start = fixed.get("start")
        if problem is None and action.action in BOOKINGS and start is not None \
                and start < now_utc - PAST_GRACE:
            problem = "past"
        if problem:
            if getattr(action, "ref", None):
                dropped_refs.add(action.ref)
            if problem not in why:
                why.append(problem)
            continue
        kept.append(action.model_copy(update=fixed) if fixed else action)

    if not why:
        questions = list(plan.questions)
        # some models ask in the summary and leave questions empty - then the person has nothing to answer
        if not kept and not questions and plan.summary.rstrip().endswith("?"):
            questions = [plan.summary.strip()[:QUESTION_MAX]]
        update = {"actions": kept, "questions": questions}
        return AssistantPlan.model_validate_json(plan.model_copy(update=update).model_dump_json())

    questions = list(plan.questions)
    for kind in why:
        if len(questions) < MAX_QUESTIONS:
            questions.append(_say(kind, "question", locale))
    notes = " ".join(_say(kind, "note", locale) for kind in why)
    writes_left = any(a.action not in ("find_client", "find_free_slot") for a in kept)
    # the model's summary describes the steps we just took out, so it only stays next to steps that remain
    summary = f"{notes} {plan.summary}".strip() if writes_left else notes
    reviewed = plan.model_copy(update={"actions": kept, "questions": questions, "summary": summary[:SUMMARY_MAX]})
    # through JSON again, so the refs and every other rule of the plan are checked once more
    return AssistantPlan.model_validate_json(reviewed.model_dump_json())
