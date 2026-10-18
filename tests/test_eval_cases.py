"""The phrase set (А4) and attack cases (Е) stay loadable and self-consistent - no model involved."""
import json
from collections import Counter

import pytest

from app.ai.plan import AssistantPlan
from evals.assistant import cases as C
from evals.assistant.fixture import all_keys, fake_fixture
from evals.assistant.scoring import score_plan

CASES = C.load_cases()
FX = fake_fixture()
NORMAL = [c for c in CASES if "attack" not in c.tags]
ATTACKS = [c for c in CASES if "attack" in c.tags]
PLAN_EXAMPLE = ("Запиши Иванова Ивана Ивановича, 19.10.2006, аллергия на димедрол, в четверг 22 октября "
                "к стоматологу Виталько на самое раннее время")


def test_set_size_and_language_mix():
    assert 60 <= len(NORMAL) <= 100
    assert len(ATTACKS) >= 20
    per_locale = Counter(c.locale for c in NORMAL)
    assert set(per_locale) == set(C.LOCALES)
    for locale, n in per_locale.items():
        assert n >= len(NORMAL) / 4, f"{locale} has only {n} cases"
    assert len({c.locale for c in ATTACKS}) == 3


def test_the_plan_example_is_in_the_set():
    assert any(c.text == PLAN_EXAMPLE for c in NORMAL)


@pytest.mark.parametrize("tag", ["new-client", "existing-client", "reschedule", "cancel", "note", "ambiguous",
                                 "missing-info", "relative-date", "medical", "typo", "earliest", "no-action", "dst"])
def test_topics_are_covered(tag):
    assert sum(tag in c.tags for c in NORMAL) >= 2


def test_cases_name_only_fixture_things():
    known = set(all_keys())
    for c in CASES:
        assert c.keys_used() <= known, c.id


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_expected_plan_is_valid_and_passes_its_own_expect(case):
    plan = C.resolve(case.plan, FX.ids, case.id)
    parsed = AssistantPlan.model_validate_json(json.dumps(plan))
    verdict = score_plan(case, parsed.model_dump(mode="json"), FX, request_text=case.text_for(FX.ids))
    assert verdict.passed, verdict.reasons


def test_relative_now_is_pinned_in_utc():
    c = next(c for c in CASES if c.id == "ru-plan-example")
    assert c.now_utc.isoformat() == "2026-10-19T07:15:00"


def test_select_filters():
    assert {c.id for c in C.select(CASES, tag="attack")} == {c.id for c in ATTACKS}
    assert all(c.locale == "cz" for c in C.select(CASES, locale="cz"))
    assert [c.id for c in C.select(CASES, case_id="ru-plan-example")] == ["ru-plan-example"]


GOOD = {"id": "en-ok-01", "locale": "en", "text": "hi", "now": "2026-10-19T09:15", "tz": "Europe/Prague",
        "expect": {"no_actions": True}, "plan": {"summary": "hi"}}


@pytest.mark.parametrize("change", [
    {"id": "Bad Id"},
    {"locale": "de"},
    {"now": "2026-10-19T09:15+02:00"},
    {"tz": "Mars/Base"},
    {"text": ""},
    {"expect": {"maybe": True}},
    {"expect": []},
    {"plan": "nope"},
    {"assistant_name": "x" * 31},
])
def test_loader_rejects_broken_cases(change):
    C.case_from_dict(GOOD)
    with pytest.raises(C.CaseError):
        C.case_from_dict({**GOOD, **change})


def test_loader_rejects_a_missing_field():
    raw = {k: v for k, v in GOOD.items() if k != "expect"}
    with pytest.raises(C.CaseError):
        C.case_from_dict(raw)


def test_duplicate_ids_are_rejected(tmp_path):
    body = {"cases": [GOOD, GOOD]}
    (tmp_path / "x.yaml").write_text(json.dumps(body), encoding="utf-8")  # JSON is YAML too
    with pytest.raises(C.CaseError):
        C.load_cases(tmp_path)


def test_unknown_fixture_key_in_text_is_an_error():
    c = C.case_from_dict({**GOOD, "text": "cancel {visit.nope}"})
    with pytest.raises(C.CaseError):
        c.text_for(FX.ids)
