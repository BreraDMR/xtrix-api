"""The model pool: rotation, cooldowns, retries and validation - all against fake transports."""
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, Field

from app.ai.config import load_ai_config
from app.ai.cooldown import CooldownBoard
from app.ai.errors import AIBusy, AINotConfigured, AIUnavailable
from app.ai.providers.base import Message, parse_retry_after
from app.ai.router import InvalidReply, build_router, parse_reply


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    word: str = Field(max_length=20)
    count: int


GOOD = {"word": "hello", "count": 2}
MSGS = [Message("system", "be json"), Message("user", "say hello twice")]


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 18, 15, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def tick(self, **kw):
        self.now += timedelta(**kw)


def env_with(n, **extra):
    env = {}
    for i in range(1, n + 1):
        env[f"LLM_{i}_BASE_URL"] = f"https://p{i}.example/v1"
        env[f"LLM_{i}_API_KEY"] = f"key-{i}"
        env[f"LLM_{i}_MODEL"] = f"model-{i}"
    env.update(extra)
    return env


def chat(content, prompt=10, completion=5):
    return httpx.Response(200, json={
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
    })


class Fake:
    """Answers per host from a queue of responses; records every request."""

    def __init__(self, plan):
        self.plan = {host: list(items) for host, items in plan.items()}
        self.calls = []

    def __call__(self, request: httpx.Request):
        self.calls.append(request)
        queue = self.plan.get(request.url.host) or []
        if not queue:
            return chat(json.dumps(GOOD))
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def hosts(self):
        return [c.url.host for c in self.calls]


def router_for(fake, env, clock=None):
    http = httpx.Client(transport=httpx.MockTransport(fake))
    return build_router(load_ai_config(env), http=http, board=CooldownBoard(clock or Clock()))


def test_first_slot_answers():
    fake = Fake({})
    result = router_for(fake, env_with(3)).generate(MSGS, Answer)
    assert result.value == Answer(**GOOD)
    assert (result.slot_index, result.model, result.prompt_tokens, result.completion_tokens) == (1, "model-1", 10, 5)
    assert fake.hosts() == ["p1.example"]
    body = json.loads(fake.calls[0].content)
    assert body["model"] == "model-1"
    assert body["response_format"] == {"type": "json_object"}
    assert fake.calls[0].headers["authorization"] == "Bearer key-1"


def test_429_moves_on_and_slot_rests_until_retry_after():
    clock = Clock()
    fake = Fake({"p1.example": [httpx.Response(429, headers={"Retry-After": "120"}, json={"error": "slow down"})]})
    router = router_for(fake, env_with(3), clock)
    assert router.generate(MSGS, Answer).slot_index == 2
    # still resting: slot 1 isn't even asked
    clock.tick(seconds=60)
    assert router.generate(MSGS, Answer).slot_index == 2
    assert fake.hosts() == ["p1.example", "p2.example", "p2.example"]
    # recovered
    clock.tick(seconds=61)
    assert router.generate(MSGS, Answer).slot_index == 1


def test_429_without_retry_after_uses_default_cooldown():
    clock = Clock()
    fake = Fake({"p1.example": [httpx.Response(429)]})
    router = router_for(fake, env_with(2, LLM_COOLDOWN_SECONDS="45"), clock)
    router.generate(MSGS, Answer)
    assert 44 < router.board.remaining("slot1") <= 45


def test_daily_quota_rests_until_utc_midnight():
    clock = Clock()  # 15:00 UTC
    fake = Fake({"p1.example": [httpx.Response(429, json={"error": {"message": "Rate limit reached on requests per day (RPD)"}})]})
    router = router_for(fake, env_with(2), clock)
    assert router.generate(MSGS, Answer).slot_index == 2
    assert router.board.remaining("slot1") == pytest.approx(9 * 3600)
    clock.tick(hours=9, seconds=1)
    assert router.generate(MSGS, Answer).slot_index == 1


def test_google_retry_delay_in_body_counts_as_retry_after():
    clock = Clock()
    body = {"error": {"status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded for metric per minute",
                      "details": [{"retryDelay": "33s"}]}}
    fake = Fake({"p1.example": [httpx.Response(429, json=body)]})
    router = router_for(fake, env_with(2), clock)
    router.generate(MSGS, Answer)
    assert router.board.remaining("slot1") == pytest.approx(33)


def test_server_errors_and_timeouts_move_on():
    fake = Fake({
        "p1.example": [httpx.Response(503)],
        "p2.example": [httpx.ReadTimeout("slow")],
        "p3.example": [httpx.ConnectError("down")],
    })
    assert router_for(fake, env_with(4)).generate(MSGS, Answer).slot_index == 4


def test_everyone_resting_is_ai_busy_with_the_shortest_wait():
    fake = Fake({
        "p1.example": [httpx.Response(429, headers={"Retry-After": "300"})],
        "p2.example": [httpx.Response(429, headers={"Retry-After": "40"})],
    })
    router = router_for(fake, env_with(2))
    with pytest.raises(AIBusy) as exc:
        router.generate(MSGS, Answer)
    assert exc.value.retry_after == 40
    # and nobody is called again while they all rest
    with pytest.raises(AIBusy):
        router.generate(MSGS, Answer)
    assert len(fake.calls) == 2


def test_invalid_json_retries_once_then_falls_back_to_next_slot():
    fake = Fake({
        "p1.example": [chat("not json at all"), chat('{"word": "hello"}')],
        "p2.example": [chat("Sure! " + json.dumps(GOOD) + " hope it helps")],
    })
    result = router_for(fake, env_with(2)).generate(MSGS, Answer)
    assert result.value == Answer(**GOOD)
    assert result.slot_index == 2
    assert result.attempts == 3
    assert result.prompt_tokens == 30  # every call is counted, failed ones too
    retry = json.loads(fake.calls[1].content)["messages"]
    assert retry[-2] == {"role": "assistant", "content": "not json at all"}
    assert "rejected" in retry[-1]["content"]


def test_retry_on_same_slot_can_fix_it():
    fake = Fake({"p1.example": [chat('{"word": "hello", "count": "2"}'), chat(json.dumps(GOOD))]})
    result = router_for(fake, env_with(2)).generate(MSGS, Answer)
    assert (result.slot_index, result.attempts) == (1, 2)
    # the fix note names the field but never echoes values
    note = json.loads(fake.calls[1].content)["messages"][-1]["content"]
    assert "count" in note


def test_all_invalid_is_unavailable_not_a_crash():
    fake = Fake({h: [chat("nope"), chat("nope")] for h in ("p1.example", "p2.example")})
    with pytest.raises(AIUnavailable) as exc:
        router_for(fake, env_with(2)).generate(MSGS, Answer)
    assert exc.value.prompt_tokens == 40


def test_200_with_error_object_or_empty_content_moves_on():
    fake = Fake({
        "p1.example": [httpx.Response(200, json={"error": {"message": "upstream"}})],
        "p2.example": [chat("")],
    })
    assert router_for(fake, env_with(3)).generate(MSGS, Answer).slot_index == 3


def test_auth_error_parks_the_slot_for_long():
    fake = Fake({"p1.example": [httpx.Response(401)]})
    router = router_for(fake, env_with(2))
    router.generate(MSGS, Answer)
    assert router.board.remaining("slot1") > 3000


def test_no_slots_is_not_configured():
    with pytest.raises(AINotConfigured):
        router_for(Fake({}), {}).generate(MSGS, Answer)


def test_slot_with_empty_key_is_skipped_and_none_means_keyless():
    env = env_with(3)
    env["LLM_1_API_KEY"] = ""
    env["LLM_2_API_KEY"] = "none"
    cfg = load_ai_config(env)
    assert [s.index for s in cfg.slots] == [2, 3]
    fake = Fake({})
    router_for(fake, env).generate(MSGS, Answer)
    assert fake.hosts() == ["p2.example"]
    assert "authorization" not in fake.calls[0].headers


def test_smart_role_uses_smart_model():
    env = env_with(1, LLM_1_MODEL_SMART="big-model")
    fake = Fake({})
    assert router_for(fake, env).generate(MSGS, Answer, role="smart").model == "big-model"
    assert json.loads(fake.calls[0].content)["model"] == "big-model"


OPENROUTER = {"LLM_1_BASE_URL": "https://openrouter.ai/api/v1", "LLM_1_API_KEY": "k", "LLM_1_MODEL": "m:free"}


def test_openrouter_allows_data_collection_by_default():
    # most free models sit behind providers that keep prompts, deny would cut them off
    fake = Fake({})
    result = router_for(fake, dict(OPENROUTER)).generate(MSGS, Answer)
    assert "provider" not in json.loads(fake.calls[0].content)
    assert result.provider == "openrouter"


def test_openrouter_can_deny_data_collection():
    env = dict(OPENROUTER, LLM_1_DATA_COLLECTION="deny")
    fake = Fake({})
    router_for(fake, env).generate(MSGS, Answer)
    assert json.loads(fake.calls[0].content)["provider"] == {"data_collection": "deny"}


def test_bad_data_collection_value_is_refused():
    with pytest.raises(ValueError):
        load_ai_config(dict(OPENROUTER, LLM_1_DATA_COLLECTION="maybe"))


def test_other_providers_get_no_openrouter_fields():
    fake = Fake({})
    router_for(fake, env_with(1)).generate(MSGS, Answer)
    assert "provider" not in json.loads(fake.calls[0].content)


def test_cost_estimate_from_slot_prices():
    env = env_with(1, LLM_1_PRICE_IN="1.0", LLM_1_PRICE_OUT="2.0")
    fake = Fake({"p1.example": [chat(json.dumps(GOOD), prompt=1000, completion=500)]})
    assert router_for(fake, env).generate(MSGS, Answer).cost_estimate == pytest.approx(0.002)


def test_logs_have_no_prompt_text(caplog):
    import logging
    logger = logging.getLogger("xtrix")
    logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.INFO, logger="xtrix"):
            fake = Fake({"p1.example": [chat("Jana Secretova is not json")]})
            router_for(fake, env_with(2)).generate([Message("user", "book Jana Secretova")], Answer)
    finally:
        logger.removeHandler(caplog.handler)
    assert "ai ok" in caplog.text
    assert "Secretova" not in caplog.text
    assert "key-1" not in caplog.text


def test_parse_reply_handles_fences_and_rejects_junk():
    assert parse_reply("```json\n" + json.dumps(GOOD) + "\n```", Answer).count == 2
    with pytest.raises(InvalidReply):
        parse_reply("[1, 2]", Answer)
    with pytest.raises(InvalidReply):
        parse_reply('{"word": "x", "count": 1, "extra": true}', Answer)


def test_retry_after_http_date():
    now = datetime(2026, 10, 18, 15, 0, tzinfo=timezone.utc)
    assert parse_retry_after("Sun, 18 Oct 2026 15:01:00 GMT", now) == 60
    assert parse_retry_after("garbage", now) is None


def test_bad_config_is_loud():
    with pytest.raises(ValueError):
        load_ai_config({"LLM_PROVIDER": "magic"})
    with pytest.raises(ValueError):
        load_ai_config(env_with(1, LLM_1_KIND="weird"))
