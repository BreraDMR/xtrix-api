"""Multi-turn assistant dialogs, driven through the real HTTP API.

The phrase evals (runner.py) check one request -> one plan. These check what a person actually
goes through: several messages in one conversation, the assistant asking back, the person changing
their mind, then confirming - and what really ended up in the database. They go through
/assistant/conversations, /assistant/plan and /assistant/execute, so history, plan review, the
executor and the error texts are all the real ones.

Every dialog gets its own fresh copy of the fixture salon with visits placed relative to today
(Prague time), so "завтра" means tomorrow and nothing from an earlier dialog gets in the way.
The rows stay in the database - point DATABASE_URL at a throwaway one (scripts/eval_dialogs.py
refuses anything that isn't on localhost).

A dialog in dialog_cases/dialogs.yaml:

    id, locale, tags
    turns     - what the person types, in order
    if_asked  - replies to use, one by one, while the assistant still only asks (optional)
    confirm   - true: run the last plan that proposes changes, then run it again (must be a replay)
    expect    - one outcome or a list of acceptable ones:
        ask: true                 the last answer asks something and proposes no change
        created: [{client, service, day, time, specialist}]   exactly these new visits
                                  (client "new:<part of name>" = a card made during the dialog)
        cancelled: [visit key]    exactly these visits cancelled
        moved: [{visit, day, time}]  exactly these visits moved
        unchanged: true           nothing in the salon changed
        nothing_proposed: true    the last answer proposes no change (asking is fine)
    Every outcome also needs: no two live visits of one master overlap, nothing in the other salon
    changed, and every text the person sees is plain language (no codes, JSON or tracebacks).
"""
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

import yaml

from evals.assistant import fixture as F

DIALOGS_FILE = Path(__file__).resolve().parent / "dialog_cases" / "dialogs.yaml"
TZ = ZoneInfo(F.FIXTURE_TZ)
WRITES = {"create_client", "update_client", "create_visit", "reschedule_visit", "cancel_visit", "add_note"}
OUTCOME_KEYS = {"ask", "created", "cancelled", "moved", "unchanged", "nothing_proposed"}

# fixture visits, re-placed relative to today: key -> (days from today, local time)
LIVE_VISITS = {
    "orlov_cleaning": (1, "09:00"),
    "petrova_haircut": (1, "10:00"),
    "clark_massage": (1, "16:00"),
    "svobodova_manicure": (2, "14:00"),
    "dusek_cut": (2, "11:00"),
    "b_visit": (1, "10:00"),
}

# what a person must never be shown: internal codes, raw data, crashes
_UNREADABLE = [
    (re.compile(r"Traceback|Exception|Error:|stack ?trace", re.I), "traceback"),
    (re.compile(r"[{}\[\]]|\bnull\b|\bNone\b|\bundefined\b"), "raw data"),
    (re.compile(r"\b[a-z]+(?:_[a-z]+){1,}\b"), "internal code"),
    (re.compile(r"\bHTTP\b|\bstatus[ _]code\b|\b(?:4\d\d|5\d\d) (?:error|Bad|Not|Internal)", re.I), "http error"),
]
_ISO = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d")


class DialogError(ValueError):
    pass


@dataclass
class Dialog:
    id: str
    locale: str
    turns: list
    expect: list
    if_asked: list = field(default_factory=list)
    confirm: bool = False
    tags: list = field(default_factory=list)


@dataclass
class Turn:
    said: str
    status: int
    summary: str = ""
    questions: list = field(default_factory=list)
    actions: list = field(default_factory=list)
    error: Optional[dict] = None
    latency_ms: int = 0
    plan_id: Optional[str] = None


@dataclass
class DialogResult:
    dialog_id: str
    locale: str
    tags: list
    status: str  # pass / fail / error
    reasons: list
    turns: list = field(default_factory=list)
    execution: Optional[dict] = None
    warnings: list = field(default_factory=list)
    db_changes: dict = field(default_factory=dict)


def load_dialogs(path: Path = DIALOGS_FILE) -> list[Dialog]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    seen, out = set(), []
    for raw in data.get("dialogs") or []:
        d = Dialog(id=raw["id"], locale=raw["locale"], turns=list(raw["turns"]),
                   expect=raw["expect"] if isinstance(raw["expect"], list) else [raw["expect"]],
                   if_asked=list(raw.get("if_asked") or []), confirm=bool(raw.get("confirm")),
                   tags=list(raw.get("tags") or []))
        if d.id in seen:
            raise DialogError(f"{d.id}: duplicate id")
        seen.add(d.id)
        if d.locale not in ("ru", "cz", "en") or not d.id.startswith(d.locale + "-"):
            raise DialogError(f"{d.id}: locale must be ru/cz/en and prefix the id")
        if not d.turns or not all(isinstance(t, str) and t.strip() for t in d.turns):
            raise DialogError(f"{d.id}: turns must be non-empty strings")
        for outcome in d.expect:
            unknown = set(outcome) - OUTCOME_KEYS
            if unknown or not outcome:
                raise DialogError(f"{d.id}: unknown outcome keys {sorted(unknown)}")
            for row in outcome.get("created") or []:
                if not str(row.get("client", "")).startswith("new:") and row.get("client") not in F.CLIENTS:
                    raise DialogError(f"{d.id}: unknown client {row.get('client')}")
                services = row.get("service")
                for s in services if isinstance(services, list) else [services]:
                    if s not in F.SERVICES:
                        raise DialogError(f"{d.id}: unknown service {s}")
                if row.get("specialist") and row["specialist"] not in F.SPECIALISTS:
                    raise DialogError(f"{d.id}: unknown specialist {row['specialist']}")
            for key in outcome.get("cancelled") or []:
                if key not in F.VISITS:
                    raise DialogError(f"{d.id}: unknown visit {key}")
            for row in outcome.get("moved") or []:
                if row.get("visit") not in F.VISITS:
                    raise DialogError(f"{d.id}: unknown visit {row.get('visit')}")
        out.append(d)
    return out


def unreadable(text: str) -> list[str]:
    return [label for pattern, label in _UNREADABLE if pattern.search(text or "")]


def _local(day: int, hhmm: str, today: date) -> datetime:
    """Naive UTC for a Prague wall-clock time `day` days from today."""
    h, m = (int(x) for x in hhmm.split(":"))
    local = datetime.combine(today + timedelta(days=day), datetime.min.time()).replace(hour=h, minute=m, tzinfo=TZ)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def seed(db, today: date) -> F.Fixture:
    """A fresh fixture salon whose visits sit around today; committed."""
    from app.db.models.business import Business
    from app.db.models.visit import Visit

    fx = F.build(db)
    for key, (day, hhmm) in LIVE_VISITS.items():
        visit = db.get(Visit, fx.ids[f"visit.{key}"])
        length = visit.date_end - visit.date_start
        visit.date_start = _local(day, hhmm, today)
        visit.date_end = visit.date_start + length
    for key in ("a", "b"):
        db.get(Business, fx.ids[f"biz.{key}"]).timezone = F.FIXTURE_TZ
    db.commit()
    return fx


def _state(db, fx: F.Fixture) -> dict:
    """Visits of both fixture salons: id -> (business key, client, services, start, status, specialist)."""
    from app.db.models.visit import Visit
    from app.db.models.visit_item import VisitItem

    biz = {fx.ids["biz.a"]: "a", fx.ids["biz.b"]: "b"}
    db.expire_all()
    rows = db.query(Visit).filter(Visit.business_id.in_(list(biz))).all()
    items = {}
    for item in db.query(VisitItem).filter(VisitItem.visit_id.in_([r.id for r in rows] or [0])).all():
        items.setdefault(item.visit_id, set()).add(item.service_id)
    state = {}
    for r in rows:
        services = set(items.get(r.id, set()))
        if r.service_id:
            services.add(r.service_id)
        alive = r.status != "cancelled" and getattr(r, "deleted_at", None) is None
        state[r.id] = dict(biz=biz[r.business_id], client=r.client_id, services=services,
                           start=r.date_start, end=r.date_end, alive=alive, specialist=r.specialist_id)
    db.rollback()
    return state


def _clients(db, fx: F.Fixture) -> dict[int, str]:
    from app.db.models.client_business_relation import ClientBusinessRelation
    from app.db.models.client import Client

    rows = (db.query(Client.id, Client.full_name)
            .join(ClientBusinessRelation, ClientBusinessRelation.client_id == Client.id)
            .filter(ClientBusinessRelation.business_id == fx.ids["biz.a"]).all())
    db.rollback()
    return {cid: name for cid, name in rows}


def _fold(text: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", (text or "").lower()) if not unicodedata.combining(c))


def judge(dialog: Dialog, turns: list[Turn], before: dict, after: dict, clients_before: dict,
          clients_after: dict, fx: F.Fixture, today: date, executed: bool) -> tuple[list[str], dict]:
    """Reasons it failed (empty = passed) against the best-matching outcome, and the DB changes."""
    # invariants first, they hold for every outcome
    hard = []
    for vid, row in after.items():
        if row["biz"] == "b" and (vid not in before or before[vid] != row):
            hard.append("the other salon was changed")
            break
    live = [r for r in after.values() if r["alive"] and r["biz"] == "a"]
    for i, x in enumerate(live):
        for y in live[i + 1:]:
            if x["specialist"] == y["specialist"] and x["start"] < y["end"] and y["start"] < x["end"]:
                hard.append(f"double booking for master {x['specialist']} at {x['start']:%m-%d %H:%M} UTC")
    created = [dict(r, id=v) for v, r in after.items() if v not in before and r["biz"] == "a"]
    cancelled = [v for v, r in after.items() if v in before and before[v]["alive"] and not r["alive"]]
    moved = [dict(r, id=v) for v, r in after.items()
             if v in before and r["alive"] and before[v]["start"] != r["start"]]
    new_clients = {cid: name for cid, name in clients_after.items() if cid not in clients_before}
    changes = {
        "created": [{"client": clients_after.get(r["client"], r["client"]), "services": sorted(r["services"]),
                     "start_utc": r["start"].isoformat(), "specialist": r["specialist"]} for r in created],
        "cancelled": cancelled,
        "moved": [{"id": r["id"], "start_utc": r["start"].isoformat()} for r in moved],
        "new_clients": list(new_clients.values()),
    }
    last = turns[-1] if turns else None
    best = None
    for outcome in dialog.expect:
        reasons = list(hard)
        if outcome.get("ask"):
            if last is None or last.status != 200:
                reasons.append("the last answer is an error, not a question")
            elif any(a.get("action") in WRITES for a in last.actions) or not last.questions:
                reasons.append("expected a question with no proposed change")
        if outcome.get("nothing_proposed") and (last is None or last.status != 200
                                                or any(a.get("action") in WRITES for a in last.actions)):
            reasons.append("the last answer still proposes a change")
        if outcome.get("unchanged") and (created or cancelled or moved or new_clients):
            reasons.append(f"expected no change, got {changes}")
        want_created = outcome.get("created")
        if want_created is not None:
            reasons += _match_created(want_created, created, fx, clients_after, new_clients, today)
        elif not outcome.get("unchanged") and created and not outcome.get("ask"):
            reasons.append(f"unexpected new visits {changes['created']}")
        if outcome.get("ask") and (created or cancelled or moved):
            reasons.append("asked, yet something changed")
        want_cancelled = outcome.get("cancelled")
        if want_cancelled is not None:
            want = sorted(fx.ids[f"visit.{k}"] for k in want_cancelled)
            if sorted(cancelled) != want:
                reasons.append(f"cancelled {sorted(cancelled)}, expected {want}")
        elif cancelled:
            reasons.append(f"unexpected cancellation {cancelled}")
        want_moved = outcome.get("moved")
        if want_moved is not None:
            got = {r["id"]: r["start"] for r in moved}
            want = {fx.ids[f"visit.{m['visit']}"]: _local(m["day"], m["time"], today) for m in want_moved}
            if got != want:
                reasons.append(f"moved {{{', '.join(f'{k}: {v:%m-%d %H:%M}' for k, v in got.items())}}} UTC, "
                               f"expected {{{', '.join(f'{k}: {v:%m-%d %H:%M}' for k, v in want.items())}}} UTC")
        elif moved:
            reasons.append(f"unexpected move {changes['moved']}")
        if (want_created or want_cancelled or want_moved) and not executed:
            reasons.append("never got to a plan to confirm")
        if best is None or len(reasons) < len(best):
            best = reasons
        if not reasons:
            break
    return best or [], changes


def _match_created(want: list, created: list, fx: F.Fixture, clients: dict, new_clients: dict,
                   today: date) -> list[str]:
    reasons = []
    left = list(created)
    for row in want:
        services = row["service"] if isinstance(row["service"], list) else [row["service"]]
        want_services = {fx.ids[f"service.{s}"] for s in services}
        target = row["client"]
        found = None
        for got in left:
            if target.startswith("new:"):
                ok_client = got["client"] in new_clients and _fold(target[4:]) in _fold(new_clients[got["client"]])
            else:
                ok_client = got["client"] == fx.ids[f"client.{target}"]
            if not ok_client:
                continue
            if row.get("day") is not None and row.get("time"):
                if got["start"] != _local(row["day"], row["time"], today):
                    continue
            if row.get("specialist") and got["specialist"] != fx.ids[f"spec.{row['specialist']}"]:
                continue
            if not got["services"] & want_services:
                continue
            found = got
            break
        if found is None:
            reasons.append(f"no new visit for {target} / {'+'.join(services)} on +{row.get('day')} "
                           f"{row.get('time')}")
            continue
        left.remove(found)
        # several procedures may be one visit with items or separate visits
        extra = want_services - found["services"]
        if extra:
            others = [g for g in left if g["client"] == found["client"] and g["services"] & extra]
            for g in others:
                extra -= g["services"]
                left.remove(g)
            if extra:
                reasons.append(f"{target}: services {sorted(extra)} missing")
    if left:
        reasons.append(f"extra new visits: {[(clients.get(g['client']), sorted(g['services']), g['start'].isoformat()) for g in left]}")
    return reasons


class DialogRunner:
    """Talks to the app through FastAPI's TestClient, as the fixture salon's owner."""

    def __init__(self, router, *, session_factory, pace: float = 0.0, max_wait: float = 120.0,
                 sleep: Callable[[float], None] = time.sleep, log: Callable[[str], None] = print):
        from fastapi.testclient import TestClient

        from app.api.v1.endpoints import assistant as endpoint
        from app.main import app

        self.app, self.endpoint = app, endpoint
        self.router = router
        self.session_factory = session_factory
        self.pace, self.max_wait, self.sleep, self.log = pace, max_wait, sleep, log
        app.dependency_overrides[endpoint.llm_router] = lambda: self.router
        self.http = TestClient(app, raise_server_exceptions=False)

    def close(self):
        self.app.dependency_overrides.pop(self.endpoint.llm_router, None)

    def _plan(self, headers, conversation, text, locale) -> Turn:
        for attempt in (1, 2, 3):
            if self.pace:
                self.sleep(self.pace)
            started = time.monotonic()
            r = self.http.post("/assistant/plan", headers=headers, json={
                "text": text, "locale": locale, "conversation_id": conversation,
                "client_request_id": str(uuid.uuid4())})
            took = int((time.monotonic() - started) * 1000)
            body = _json(r)
            if r.status_code in (429, 503) and attempt < 3:
                code = _code(body)
                wait = float(r.headers.get("Retry-After") or 30)
                if code in ("ai_busy", "ai_unavailable") and wait <= self.max_wait:
                    self.log(f"    model busy ({code}), waiting {wait:.0f}s")
                    self.sleep(wait + 1)
                    self.router.board = type(self.router.board)()
                    continue
            if r.status_code != 200:
                return Turn(text, r.status_code, error=body if isinstance(body, dict) else {"raw": str(body)[:300]},
                            latency_ms=took)
            return Turn(text, 200, summary=body.get("summary") or "", questions=list(body.get("questions") or []),
                        actions=list(body.get("actions") or []), latency_ms=took, plan_id=body.get("plan_id"))
        raise AssertionError("unreachable")

    def run(self, dialog: Dialog) -> DialogResult:
        from app.api.core.security import create_access_token

        today = datetime.now(TZ).date()
        with self.session_factory() as db:
            fx = seed(db, today)
            before, clients_before = _state(db, fx), _clients(db, fx)
        headers = {"Authorization": f"Bearer {create_access_token({'sub': str(fx.owner_user_id)})}",
                   "Accept-Language": dialog.locale}
        conv = self.http.post("/assistant/conversations", headers=headers,
                              json={"client_request_id": str(uuid.uuid4())})
        if conv.status_code != 201:
            return DialogResult(dialog.id, dialog.locale, dialog.tags, "error",
                                [f"conversation: {conv.status_code} {conv.text[:200]}"])
        conversation = conv.json()["id"]
        turns, warnings, reasons = [], [], []
        queue = list(dialog.turns)
        replies = list(dialog.if_asked)
        while queue:
            turn = self._plan(headers, conversation, queue.pop(0), dialog.locale)
            turns.append(turn)
            if turn.status != 200:
                break
            asks_only = not any(a.get("action") in WRITES for a in turn.actions) and turn.questions
            if not queue and asks_only and replies and (dialog.confirm or not _wants_ask(dialog)):
                queue.append(replies.pop(0))
        status = "pass"
        for turn in turns:
            shown = [turn.summary, *turn.questions]
            if turn.error:
                message = _message(turn.error)
                shown.append(message)
                if turn.status >= 500 and _code(turn.error) not in ("ai_busy", "ai_unavailable", "ai_not_configured"):
                    reasons.append(f"server error {turn.status} on '{turn.said[:40]}'")
            for text in shown:
                bad = unreadable(text)
                if bad:
                    reasons.append(f"unreadable ({', '.join(bad)}): {text[:120]!r}")
                if _ISO.search(text or ""):
                    warnings.append(f"ISO time shown: {text[:80]!r}")
        if turns and turns[-1].error and _code(turns[-1].error) in ("ai_busy", "ai_unavailable", "ai_limit_reached"):
            status = "error"
            reasons.append(f"model unavailable: {_code(turns[-1].error)}")
        execution = None
        executed = False
        if dialog.confirm and status != "error":
            plan_turn = next((t for t in reversed(turns) if t.status == 200
                              and any(a.get("action") in WRITES for a in t.actions)), None)
            last_plan_id = plan_turn.plan_id if plan_turn else None
            if last_plan_id:
                first = self.http.post("/assistant/execute", headers=headers, json={"plan_id": last_plan_id})
                execution = _json(first)
                executed = True
                with self.session_factory() as db:
                    once = _state(db, fx)
                again = self.http.post("/assistant/execute", headers=headers, json={"plan_id": last_plan_id})
                with self.session_factory() as db:
                    twice = _state(db, fx)
                if _json(again) != execution or once != twice:
                    reasons.append("running the same plan twice changed something")
                if first.status_code >= 500:
                    reasons.append(f"execute: server error {first.status_code}")
                for item in (execution or {}).get("results") or []:
                    if item.get("error"):
                        bad = unreadable(item["error"].get("message", ""))
                        if bad:
                            reasons.append(f"unreadable execute error ({', '.join(bad)}): "
                                           f"{item['error'].get('message', '')[:120]!r}")
        with self.session_factory() as db:
            after, clients_after = _state(db, fx), _clients(db, fx)
        if status != "error":
            outcome_reasons, changes = judge(dialog, turns, before, after, clients_before, clients_after, fx,
                                             today, executed)
            reasons += outcome_reasons
            if reasons:
                status = "fail"
        else:
            changes = {}
        return DialogResult(dialog.id, dialog.locale, dialog.tags, status, reasons,
                            turns=[asdict(t) for t in turns], execution=execution, warnings=warnings,
                            db_changes=changes)


def _wants_ask(dialog: Dialog) -> bool:
    return all(o.get("ask") for o in dialog.expect)


def _json(response):
    try:
        return response.json()
    except ValueError:
        return {"raw": response.text[:300]}


def _code(body) -> str:
    # errors come as {"error": {"message", "detail": {"code", ...}}}; FastAPI's own as {"detail": {...}}
    if not isinstance(body, dict):
        return ""
    error = body.get("error") if isinstance(body.get("error"), dict) else body
    detail = error.get("detail")
    if isinstance(detail, dict) and detail.get("code"):
        return str(detail["code"])
    return str(error.get("code") or "")


def _message(body) -> str:
    if not isinstance(body, dict):
        return str(body)
    detail = body.get("detail")
    if isinstance(detail, dict) and detail.get("message"):
        return str(detail["message"])
    error = body.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or "")
    return str(body.get("message") or "")
