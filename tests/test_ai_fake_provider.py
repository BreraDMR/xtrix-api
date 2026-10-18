"""LLM_PROVIDER=fake reads the plan from a file - for web E2E and demos, never in production."""
import json

import pytest

from app.ai.config import load_ai_config
from app.ai.errors import AINotConfigured, AIUnavailable
from app.ai.plan import AssistantPlan
from app.ai.providers.base import Message
from app.ai.router import build_router

PLAN = {"summary": "Book Jana", "questions": [],
        "actions": [{"action": "find_client", "ref": "c", "name": "Jana"}]}
MESSAGES = [Message("system", "s"), Message("user", "u")]


def fake_env(path, **extra):
    return {"LLM_PROVIDER": "fake", "LLM_FAKE_PLAN_FILE": str(path), **extra}


def test_fake_provider_answers_with_the_file(tmp_path):
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps(PLAN))
    router = build_router(load_ai_config(fake_env(plan_file)))
    result = router.generate(MESSAGES, AssistantPlan)
    assert result.value.summary == "Book Jana"
    assert (result.provider, result.prompt_tokens, result.completion_tokens) == ("fake", 0, 0)
    # read on every call, so a test can swap it
    plan_file.write_text(json.dumps({**PLAN, "summary": "Second"}))
    assert router.generate(MESSAGES, AssistantPlan).value.summary == "Second"


def test_fake_plan_is_validated_like_any_model_output(tmp_path):
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps({"summary": "s", "actions": [{"action": "update_client", "client_id": 1,
                                                                  "allergies": "dimedrol"}]}))
    router = build_router(load_ai_config(fake_env(plan_file)))
    with pytest.raises(AIUnavailable):
        router.generate(MESSAGES, AssistantPlan)


def test_missing_file_is_unavailable_not_a_crash(tmp_path):
    router = build_router(load_ai_config(fake_env(tmp_path / "nope.json")))
    with pytest.raises(AIUnavailable):
        router.generate(MESSAGES, AssistantPlan)
    assert router.board.remaining("slot1") == 0  # no long rest, the file may appear


def test_fake_provider_refuses_production(tmp_path):
    with pytest.raises(AINotConfigured):
        build_router(load_ai_config(fake_env(tmp_path / "plan.json", ENVIRONMENT="production")))


def test_fake_provider_needs_a_file():
    with pytest.raises(AINotConfigured):
        build_router(load_ai_config({"LLM_PROVIDER": "fake"}))
