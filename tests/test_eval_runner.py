"""scripts/eval_assistant.py end to end, without any real model: the fake modes test the scorer,
a mocked slot tests the per-slot numbers, and nothing is left in the database."""
import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from app.ai import config as ai_config
from app.db.models.ai_usage import AIUsage
from app.db.models.business import Business
from app.db.session import engine
from evals.assistant import runner
from evals.assistant.cases import load_cases

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "eval_assistant.py"
SLOT_ENV = {"LLM_PROVIDER": "pool", "LLM_1_BASE_URL": "https://p1.example/v1", "LLM_1_API_KEY": "k1",
            "LLM_1_MODEL": "m1", "LLM_3_BASE_URL": "https://p3.example/v1", "LLM_3_API_KEY": "k3",
            "LLM_3_MODEL": "m3"}


def _load():
    spec = importlib.util.spec_from_file_location("eval_assistant", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _counts():
    with engine.connect() as conn:
        return tuple(conn.execute(select(func.count()).select_from(m.__table__)).scalar() for m in (Business, AIUsage))


def _report(tmp_path) -> dict:
    files = list(tmp_path.glob("assistant-*.json"))
    assert len(files) == 1
    return json.loads(files[0].read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    # the runner reads .env and the process env; pin both to "nothing configured" unless a test says so
    real = ai_config.load_ai_config
    state = {"env": {}}
    monkeypatch.setattr(ai_config, "load_ai_config", lambda env=None: real(state["env"] if env is None else env))
    return state


def test_fake_mode_scores_everything_and_leaves_nothing(tmp_path):
    before = _counts()
    lines = []
    code = _load().main(["--fake", "--report-dir", str(tmp_path)], log=lines.append)
    assert code == 0, "\n".join(lines)
    report = _report(tmp_path)
    summary = report["slots"][0]["summary"]
    assert summary["cases"] == len(load_cases())
    assert summary["pass_pct"] == 100.0
    assert summary["invalid_json"] == 0 and summary["errors"] == 0
    assert _counts() == before


def test_fake_bad_mode_is_caught(tmp_path):
    lines = []
    code = _load().main(["--fake-bad", "--report-dir", str(tmp_path)], log=lines.append)
    assert code == 1
    results = _report(tmp_path)["slots"][0]["results"]
    assert sum(r["status"] == "pass" for r in results) < len(results)
    # three of the four kinds of broken plan break a global rule, so they must fail every time
    for n, r in enumerate(results):
        if n % 4 != 1:
            assert r["status"] == "fail", r["case_id"]
    reasons = " ".join(" ".join(r["reasons"]) for r in results)
    assert "foreign id" in reasons and "medical word" in reasons and "unknown id" in reasons


def test_filters_pick_cases(tmp_path):
    code = _load().main(["--fake", "--tag", "attack", "--locale", "cz", "--report-dir", str(tmp_path)],
                        log=lambda _: None)
    assert code == 0
    report = _report(tmp_path)
    assert report["meta"]["cases"] and all(c.startswith("cz-") for c in report["meta"]["cases"])
    assert len(report["slots"][0]["results"]) == len(report["meta"]["cases"])


def test_no_slots_means_exit_2(tmp_path):
    lines = []
    assert _load().main(["--report-dir", str(tmp_path)], log=lines.append) == 2
    assert "No model slots are configured" in lines[0]
    assert not list(tmp_path.iterdir())


def test_unknown_slot_and_unknown_case(tmp_path, _env):
    _env["env"] = SLOT_ENV
    lines = []
    assert _load().main(["--slot", "2", "--report-dir", str(tmp_path)], log=lines.append) == 2
    assert "[2]" in lines[0] and "[1, 3]" in lines[0]
    assert _load().main(["--fake", "--case", "xx-nope"], log=lines.append) == 2


def test_one_slot_through_a_mocked_provider(tmp_path, _env, monkeypatch):
    _env["env"] = SLOT_ENV
    hosts = []
    replies = iter(["not json at all", json.dumps({"summary": "Hi!", "questions": [], "actions": []})])

    def model(request):
        hosts.append(request.url.host)
        body = {"choices": [{"message": {"content": next(replies)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10}}
        return httpx.Response(200, json=body)

    real_build = runner.build_router
    monkeypatch.setattr(runner, "build_router", lambda cfg, board=None: real_build(
        cfg, board=board, http=httpx.Client(transport=httpx.MockTransport(model))))
    code = _load().main(["--slot", "3", "--case", "en-small-talk", "--report-dir", str(tmp_path)],
                        log=lambda _: None)
    assert code == 0
    assert hosts == ["p3.example", "p3.example"]  # only slot 3, and the retry after the bad JSON
    summary = _report(tmp_path)["slots"][0]["summary"]
    assert summary["slot"] == "slot3" and summary["model"] == "m3"
    assert summary["invalid_json"] == 1 and summary["passed"] == 1
    assert summary["prompt_tokens"] == 200 and summary["completion_tokens"] == 20


def test_rate_limited_slot_waits_once_then_skips(tmp_path, _env, monkeypatch):
    _env["env"] = SLOT_ENV
    calls = []

    def model(request):
        calls.append(1)
        return httpx.Response(429, headers={"retry-after": "5"}, json={"error": "slow down"})

    real_build = runner.build_router
    monkeypatch.setattr(runner, "build_router", lambda cfg, board=None: real_build(
        cfg, board=board, http=httpx.Client(transport=httpx.MockTransport(model))))
    waits = []
    reports = runner.run(load_cases()[:3], config=ai_config.load_ai_config(SLOT_ENV),
                         slots=[ai_config.load_ai_config(SLOT_ENV).slots[0]], sleep=waits.append, log=lambda _: None)
    statuses = [r.status for r in reports[0].results]
    assert statuses == ["error", "error", "error"]
    assert waits == [6] and len(calls) == 2
    assert "skipped" in reports[0].results[1].reasons[0]
