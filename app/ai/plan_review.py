"""A look at the model's plan before the person sees it (duty plan item 8).

The executor is still the real guard - this only stops us from showing steps that can't work or
that are obviously off, and turns them into a plain question instead:

- ids that aren't in the user's own salon (or don't exist) - the step is dropped, and so is every
  step built on its ref. Usually that's a prompt injection or a made-up id.
- a time with the wrong summer/winter offset for that date ("10:00+02:00" on 27.10 in Prague):
  the person said 10:00, so we keep the wall clock and take the salon's offset for that day.
  A plain UTC time (or any offset the salon never uses) is a real instant and stays as it is.
- a booking or reschedule that's already in the past, beyond a short grace - dropped with a question.
- a booking or reschedule onto a time the master already has taken - dropped, and the question says so
  and offers the next free times, instead of failing after the person confirmed.
- a find_client that finds nobody, or several people - the executor would fail on it after the person
  confirmed, so it becomes "nobody called X" or "which one: A, B?" right away.
- record numbers the model quoted back ("(ID 747)", "client_id=12") are taken out of the text.
- a plan with nothing to do whose summary is a question: the summary goes into questions too.

Reads only, through the same permission helpers as the API.
"""
import re
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
    "no_client": {
        "note": {
            "en": "That client isn't in your list, so the step is not in the plan.",
            "ru": "Такого клиента в вашей базе нет, поэтому этого шага нет в плане.",
            "cz": "Takový klient ve vašem seznamu není, proto tento krok v plánu není.",
        },
        "question": {
            "en": "No client called \u201c{who}\u201d was found. Could you check the name or give a phone "
                  "number - or should a new client be added?",
            "ru": "Клиента \u00ab{who}\u00bb не нашлось. Уточните имя или телефон - или добавить нового клиента?",
            "cz": "Klient \u201e{who}\u201c se nenašel. Upřesněte jméno nebo telefon - nebo mám přidat nového klienta?",
        },
    },
    "many_clients": {
        "note": {
            "en": "Several clients fit the name, so the step is not in the plan yet.",
            "ru": "Под это имя подходят несколько клиентов, поэтому шага пока нет в плане.",
            "cz": "Jménu odpovídá víc klientů, proto krok zatím v plánu není.",
        },
        "question": {
            "en": "Several clients match \u201c{who}\u201d: {names}. Which one did you mean?",
            "ru": "Под \u00ab{who}\u00bb подходят несколько клиентов: {names}. Кого именно вы имели в виду?",
            "cz": "Na \u201e{who}\u201c sedí víc klientů: {names}. Kterého jste měli na mysli?",
        },
    },
    "busy": {
        "note": {
            "en": "The master already has a booking at that time, so it is not in the plan.",
            "ru": "У мастера на это время уже есть запись, поэтому её нет в плане.",
            "cz": "Pracovník má na tento čas už jinou rezervaci, proto v plánu není.",
        },
        "question": {
            "en": "{who} is already booked on {day} at {time}.{free} Which time should I use?",
            "ru": "{who} {day} в {time} уже занят(а).{free} На какое время записать?",
            "cz": "{who} je {day} v {time} už obsazen(a).{free} Na jaký čas to mám dát?",
        },
        "free": {
            "en": " Free: {times}.",
            "ru": " Свободно: {times}.",
            "cz": " Volno: {times}.",
        },
    },
}
MAX_NAMES = 5
FREE_TIMES = 3

# models sometimes quote our record numbers back ("Olga Belova (ID 747)"); a person never needs them
_ID_IN_BRACKETS = re.compile(r"\s*\(\s*id\s*[:#=]?\s*\d+\s*\)", re.I)
_ID_WORD = re.compile(r"\s*\bid\s*[:#=]?\s*\d+\b", re.I)
_ID_FIELD = re.compile(r"\b([a-z]+)_id\s*[:=]?\s*\d+", re.I)


def _plain(text: str) -> str:
    text = _ID_FIELD.sub(r"\1", _ID_WORD.sub("", _ID_IN_BRACKETS.sub("", text)))
    return re.sub(r"\s+([.,;:!?])", r"\1", re.sub(r"[ \t]{2,}", " ", text)).strip()


def _finish(plan: AssistantPlan, update: dict) -> AssistantPlan:
    update["summary"] = _plain(update.get("summary", plan.summary))
    update["questions"] = [q for q in (_plain(q) for q in update.get("questions", plan.questions)) if q]
    # through JSON again, so the refs and every other rule of the plan are checked once more
    return AssistantPlan.model_validate_json(plan.model_copy(update=update).model_dump_json())


def _say(kind: str, part: str, locale: str, **values) -> str:
    texts = _TEXT[kind][part]
    return texts.get(locale, texts["en"]).format(**values)


def _busy(db: Session, user: User, action, start: datetime, zone: ZoneInfo, locale: str) -> Optional[dict]:
    """The master is taken at a booking's time: what to tell the person, else None.
    Same overlap check as saving the visit, and free times from the same search as find_free_slot."""
    from app.ai.executor import DEFAULT_MINUTES
    from app.api.core.slot_search import find_staff_slot
    from app.api.crud.crud_visit import find_overlapping_visit

    begin = start.astimezone(timezone.utc).replace(tzinfo=None)
    service = None
    if action.action == "create_visit":
        specialist_id = action.specialist_id or user.id
        if action.service_id:
            service = db.get(Service, action.service_id)
        minutes = action.duration_minutes or (service.duration_minutes if service and service.duration_minutes
                                              else DEFAULT_MINUTES)
        exclude = None
    else:
        visit = db.get(Visit, action.visit_id)
        if visit is None:
            return None
        specialist_id = visit.specialist_id
        minutes = int(((visit.date_end - visit.date_start).total_seconds() if visit.date_end else DEFAULT_MINUTES * 60) // 60)
        exclude = visit.id
    if not find_overlapping_visit(db, specialist_id, begin, begin + timedelta(minutes=minutes), exclude):
        return None
    times, after = [], begin
    for _ in range(FREE_TIMES):
        found = find_staff_slot(db, specialist_id, service=service, duration_minutes=minutes, start_from=after,
                                days_ahead=7, timezone_name=str(zone))
        if not found:
            break
        times.append(found)
        after = found + timedelta(minutes=minutes)
    master = db.get(User, specialist_id)
    when = _local(begin, zone)
    # free times on the same day read as just the hour, other days get the date too
    labels = [t.strftime("%H:%M") if t.date() == when.date() else t.strftime("%d.%m %H:%M")
              for t in (_local(x, zone) for x in times)]
    free = _say("busy", "free", locale, times=", ".join(labels)) if labels else ""
    return {"who": master.full_name if master and master.full_name else "?",
            "day": when.strftime("%d.%m"), "time": when.strftime("%H:%M"), "free": free}


def _local(value: datetime, zone: ZoneInfo) -> datetime:
    return value.replace(tzinfo=timezone.utc).astimezone(zone)


def _lookup(db: Session, user: User, action) -> tuple[Optional[str], dict]:
    """A find_client the executor would not resolve to exactly one client: why, and what to say."""
    from app.ai.client_search import find_clients

    who = (action.name or action.phone or action.email or "").strip()
    rows = find_clients(db, user, name=action.name, phone=action.phone, email=action.email)
    who = who.replace("\u00ab", "").replace("\u00bb", "").replace('"', "")[:60]
    if not rows:
        return "no_client", {"who": who}
    if len(rows) > 1:
        names = ", ".join(name for _, name in rows[:MAX_NAMES]) + ("..." if len(rows) > MAX_NAMES else "")
        return "many_clients", {"who": who, "names": names}
    return None, {}


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
    asked = []  # (kind, values) for find_client questions, they name who was looked for

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
        if problem is None and action.action in BOOKINGS and start is not None:
            taken = _busy(db, user, action, start, zone, locale)
            if taken:
                problem = "busy"
                asked.append(("busy", taken))
        if problem is None and action.action == "find_client":
            problem, values = _lookup(db, user, action)
            if problem:
                asked.append((problem, values))
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
        return _finish(plan, {"actions": kept, "questions": questions})

    questions = list(plan.questions)
    for kind, values in asked:
        # the concrete "who did you mean" goes first, the model's own questions may be about other things
        questions.insert(0, _say(kind, "question", locale, **values)[:QUESTION_MAX])
    for kind in why:
        if kind not in ("no_client", "many_clients", "busy") and len(questions) < MAX_QUESTIONS:
            questions.append(_say(kind, "question", locale))
    questions = questions[:MAX_QUESTIONS]
    notes = " ".join(_say(kind, "note", locale) for kind in why)
    writes_left = any(a.action not in ("find_client", "find_free_slot") for a in kept)
    # the model's summary describes the steps we just took out, so it only stays next to steps that remain
    summary = f"{notes} {plan.summary}".strip() if writes_left else notes
    return _finish(plan, {"actions": kept, "questions": questions, "summary": summary[:SUMMARY_MAX]})
