"""Runs the cases through app.ai.assistant.make_plan, one model slot at a time, and scores them.

The CLI is scripts/eval_assistant.py; this module is what it (and the tests) call.
Fake modes don't need any keys: "fake" answers every case with its own expected plan (the scorer must
give 100%), "fake-bad" answers with broken plans (the scorer must catch them).
"""
import json
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from app.ai.config import AIConfig, SlotConfig
from app.ai.cooldown import CooldownBoard
from app.ai.errors import AIBusy, AIError
from app.ai.plan import AssistantPlan
from app.ai.providers.base import Completion
from app.ai.router import InvalidReply, LLMRouter, Slot, build_router, parse_reply
from evals.assistant import fixture as F
from evals.assistant.cases import Case, resolve
from evals.assistant.scoring import score_plan

REPORTS_DIR = Path(__file__).resolve().parents[1] / "reports"
FAKE_MODES = ("fake", "fake-bad")


@dataclass
class CaseResult:
    case_id: str
    locale: str
    tags: list
    status: str  # pass / fail / error
    reasons: list
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    replies: int = 0
    invalid_replies: int = 0
    plan: Optional[dict] = None


@dataclass
class SlotReport:
    label: str
    provider: str
    model: str
    results: list = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(r.status == "pass" for r in self.results)

    @property
    def errors(self) -> int:
        return sum(r.status == "error" for r in self.results)

    @property
    def pass_pct(self) -> float:
        return round(100.0 * self.passed / self.total, 1) if self.total else 0.0

    @property
    def invalid_json(self) -> int:
        return sum(r.invalid_replies for r in self.results)

    @property
    def avg_latency_ms(self) -> int:
        timed = [r.latency_ms for r in self.results if r.replies]
        return int(sum(timed) / len(timed)) if timed else 0

    @property
    def tokens(self) -> tuple[int, int]:
        return (sum(r.prompt_tokens for r in self.results), sum(r.completion_tokens for r in self.results))

    def summary(self) -> dict:
        tin, tout = self.tokens
        return {"slot": self.label, "provider": self.provider, "model": self.model, "cases": self.total,
                "passed": self.passed, "pass_pct": self.pass_pct, "errors": self.errors,
                "invalid_json": self.invalid_json, "avg_latency_ms": self.avg_latency_ms,
                "prompt_tokens": tin, "completion_tokens": tout}


class Recorder:
    """Wraps a slot's adapter to count replies, invalid JSON and tokens for the current case."""

    def __init__(self, inner):
        self.inner = inner
        self.reset()

    def reset(self):
        self.replies = self.invalid = self.prompt_tokens = self.completion_tokens = 0

    def complete(self, messages, **kwargs) -> Completion:
        reply = self.inner.complete(messages, **kwargs)
        self.replies += 1
        self.prompt_tokens += reply.prompt_tokens
        self.completion_tokens += reply.completion_tokens
        try:
            parse_reply(reply.text, AssistantPlan)
        except InvalidReply:
            self.invalid += 1
        return reply


class FakeModel:
    """Says whatever the runner put in .reply - no network."""

    def __init__(self):
        self.reply = "{}"

    def complete(self, messages, **kwargs) -> Completion:
        size = sum(len(m.content) for m in messages)
        return Completion(self.reply, prompt_tokens=size // 4, completion_tokens=len(self.reply) // 4)


def bad_plan(case: Case, fx: F.Fixture, n: int) -> dict:
    """A plan that breaks the rules in one of four ways, picked by n."""
    good = resolve(case.plan, fx.ids, case.id)
    actions = list(good.get("actions") or [])
    some_client = fx.ids["client.anna_petrova"]
    kind = n % 4
    if kind == 0:
        actions.append({"action": "cancel_visit", "visit_id": fx.ids["visit.b_visit"]})
        return {**good, "actions": actions}
    if kind == 1:
        return {"summary": "done", "questions": [], "actions": []}
    if kind == 2:
        actions.append({"action": "create_visit", "client_id": some_client, "start": "2026-10-30T10:00:00+01:00",
                        "note": "аллергия на латекс"})
        return {**good, "actions": actions}
    actions.append({"action": "cancel_visit", "visit_id": 2_000_000_000})
    return {**good, "actions": actions}


def _eval_config(config: AIConfig) -> AIConfig:
    # the fixture's plan gets the default limit; make it large enough for any number of runs
    return replace(config, monthly_limit_default=10**9, monthly_limits={})


def _routers(config: AIConfig, slots: list[SlotConfig], mode: Optional[str]):
    """(label, provider, model, router, recorder, fake) per slot."""
    cfg = _eval_config(config)
    if mode in FAKE_MODES:
        fake = FakeModel()
        rec = Recorder(fake)
        slot_cfg = SlotConfig(index=0, kind="fake", base_url="", api_key=None, model_fast=mode, model_smart=mode,
                              name="fake")
        yield "fake", "fake", mode, LLMRouter([Slot(slot_cfg, rec)], config=cfg, board=CooldownBoard()), rec, fake
        return
    for slot_cfg in slots:
        router = build_router(replace(cfg, provider="pool", slots=(slot_cfg,)), board=CooldownBoard())
        rec = Recorder(router.slots[0].adapter)
        router.slots[0].adapter = rec
        yield slot_cfg.label, slot_cfg.name, slot_cfg.model_fast, router, rec, None


def run(cases: list[Case], *, config: AIConfig, slots: list[SlotConfig], mode: Optional[str] = None,
        role: str = "fast", engine=None, max_wait: float = 90.0, sleep: Callable[[float], None] = time.sleep,
        log: Callable[[str], None] = print) -> list[SlotReport]:
    from app.ai.assistant import PlanStore, make_plan
    from app.db.models.user import User

    reports = []
    with F.rolled_back(engine) as db:
        fx = F.build(db)
        owner = db.get(User, fx.owner_user_id)
        current_name = None
        for label, provider, model, router, rec, fake in _routers(config, slots, mode):
            report = SlotReport(label, provider, model)
            reports.append(report)
            log(f"--> {label} ({provider} / {model}): {len(cases)} cases")
            gave_up = False
            for n, case in enumerate(cases):
                if gave_up:
                    report.results.append(CaseResult(case.id, case.locale, case.tags, "error",
                                                     ["skipped: the slot is cooling down"]))
                    continue
                if case.assistant_name != current_name:
                    F.set_assistant_name(db, fx, case.assistant_name)
                    current_name = case.assistant_name
                text = case.text_for(fx.ids)
                if fake is not None:
                    answer = resolve(case.plan, fx.ids, case.id) if mode == "fake" else bad_plan(case, fx, n)
                    fake.reply = json.dumps(answer, ensure_ascii=False)
                result = None
                for attempt in (1, 2):
                    rec.reset()
                    started = time.monotonic()
                    try:
                        draft = make_plan(db, owner, text, locale=case.locale, tz=case.tz, now=case.now_utc,
                                          role=role, router=router, store=PlanStore(), config=router.config)
                    except AIBusy as busy:
                        if attempt == 1 and busy.retry_after <= max_wait:
                            log(f"    {case.id}: slot busy, waiting {busy.retry_after}s")
                            sleep(busy.retry_after + 1)
                            router.board = CooldownBoard()
                            continue
                        result = CaseResult(case.id, case.locale, case.tags, "error", [f"ai_busy ({busy.retry_after}s)"])
                        gave_up = True
                    except AIError as err:
                        result = CaseResult(case.id, case.locale, case.tags, "error", [err.code])
                    else:
                        plan = draft.plan.model_dump(mode="json")
                        verdict = score_plan(case, plan, fx, request_text=text)
                        result = CaseResult(case.id, case.locale, case.tags, "pass" if verdict.passed else "fail",
                                            verdict.reasons, plan=plan)
                    result.latency_ms = int((time.monotonic() - started) * 1000)
                    break
                result.replies, result.invalid_replies = rec.replies, rec.invalid
                result.prompt_tokens, result.completion_tokens = rec.prompt_tokens, rec.completion_tokens
                report.results.append(result)
                if result.status != "pass":
                    log(f"    {result.status.upper()} {case.id}: {'; '.join(result.reasons)}")
    return reports


def format_table(reports: list[SlotReport]) -> str:
    head = ("slot", "provider", "model", "cases", "pass", "pass %", "bad JSON", "errors", "avg ms", "tokens in/out")
    rows = [head]
    for r in reports:
        s = r.summary()
        rows.append((s["slot"], s["provider"], s["model"][:32], str(s["cases"]), str(s["passed"]),
                     f"{s['pass_pct']:.1f}", str(s["invalid_json"]), str(s["errors"]), str(s["avg_latency_ms"]),
                     f"{s['prompt_tokens']}/{s['completion_tokens']}"))
    widths = [max(len(row[i]) for row in rows) for i in range(len(head))]
    lines = ["  ".join(cell.ljust(w) for cell, w in zip(row, widths, strict=True)).rstrip() for row in rows]
    lines.insert(1, "  ".join("-" * w for w in widths))
    return "\n".join(lines)


def write_report(reports: list[SlotReport], meta: dict, directory: Path = REPORTS_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = directory / f"assistant-{stamp}-{meta.get('mode') or 'models'}.json"
    body = {"meta": meta, "slots": [{"summary": r.summary(), "results": [asdict(x) for x in r.results]}
                                    for r in reports]}
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
