"""Anthropic and CLI adapters: request shape, error mapping, the production guard."""
import json
import subprocess

import httpx
import pytest
from pydantic import BaseModel

from app.ai.config import load_ai_config
from app.ai.cooldown import CooldownBoard
from app.ai.errors import AINotConfigured
from app.ai.providers.anthropic import AnthropicAdapter
from app.ai.providers.base import Message, ProviderError
from app.ai.providers.cli import CLIAdapter
from app.ai.router import build_router


class Answer(BaseModel):
    word: str


MSGS = [Message("system", "rules one"), Message("system", "rules two"),
        Message("user", "hi"), Message("user", "again")]


def anthropic_reply(text='{"word": "hej"}', **extra):
    return httpx.Response(200, json={
        "id": "msg_1", "type": "message", "role": "assistant", "stop_reason": "end_turn",
        "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": text}],
        "usage": {"input_tokens": 12, "output_tokens": 7}, **extra,
    })


def test_anthropic_request_shape():
    seen = []

    def handler(request):
        seen.append(request)
        return anthropic_reply()

    adapter = AnthropicAdapter("test-key", http=httpx.Client(transport=httpx.MockTransport(handler)))
    out = adapter.complete(MSGS, model="claude-haiku-5-5", json_schema=None, max_tokens=900, timeout=5)
    req = seen[0]
    assert str(req.url) == "https://api.anthropic.com/v1/messages"
    assert req.headers["x-api-key"] == "test-key"
    assert req.headers["anthropic-version"] == "2023-06-01"
    assert "authorization" not in req.headers
    body = json.loads(req.content)
    assert body == {
        "model": "claude-haiku-5-5",
        "max_tokens": 900,
        "system": "rules one\n\nrules two",
        # same-role turns are merged, system never goes into messages
        "messages": [{"role": "user", "content": "hi\n\nagain"}],
    }
    assert (out.text, out.prompt_tokens, out.completion_tokens) == ('{"word": "hej"}', 12, 7)


@pytest.mark.parametrize("status,kind", [(429, "rate_limited"), (529, "server"), (500, "server"),
                                         (401, "auth"), (400, "bad_request")])
def test_anthropic_errors_map_to_kinds(status, kind):
    adapter = AnthropicAdapter("k", http=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(status, headers={"retry-after": "7"}, json={"type": "error"}))))
    with pytest.raises(ProviderError) as exc:
        adapter.complete(MSGS, model="m", json_schema=None, max_tokens=10, timeout=5)
    assert exc.value.kind == kind


def test_anthropic_refusal_is_a_bad_response():
    adapter = AnthropicAdapter("k", http=httpx.Client(transport=httpx.MockTransport(
        lambda r: anthropic_reply("", stop_reason="refusal"))))
    with pytest.raises(ProviderError) as exc:
        adapter.complete(MSGS, model="m", json_schema=None, max_tokens=10, timeout=5)
    assert exc.value.kind == "bad_response"


def test_anthropic_provider_with_fallback_model():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body["model"])
        if body["model"] == "main-model":
            return httpx.Response(529, json={"type": "error", "error": {"type": "overloaded_error"}})
        return anthropic_reply()

    cfg = load_ai_config({"LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "k",
                          "ANTHROPIC_MODEL_FAST": "main-model", "ANTHROPIC_MODEL_FALLBACK": "spare-model"})
    router = build_router(cfg, http=httpx.Client(transport=httpx.MockTransport(handler)), board=CooldownBoard())
    result = router.generate(MSGS, Answer)
    assert calls == ["main-model", "spare-model"]
    assert (result.provider, result.model) == ("anthropic", "spare-model")


def test_anthropic_without_key_has_no_slots():
    assert load_ai_config({"LLM_PROVIDER": "anthropic"}).slots == ()


class FakeRun:
    def __init__(self, stdout='{"word": "cli"}', returncode=0, exc=None):
        self.stdout, self.returncode, self.exc = stdout, returncode, exc
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if self.exc:
            raise self.exc
        return subprocess.CompletedProcess(argv, self.returncode, stdout=self.stdout, stderr="")


def test_cli_refuses_to_start_in_production():
    with pytest.raises(AINotConfigured):
        CLIAdapter("claude --print", environment="production", runner=FakeRun())
    cfg = load_ai_config({"LLM_PROVIDER": "cli", "ENVIRONMENT": "production"})
    with pytest.raises(AINotConfigured):
        build_router(cfg, cli_runner=FakeRun())


def test_cli_runs_the_command_with_prompt_on_stdin():
    run = FakeRun()
    cfg = load_ai_config({"LLM_PROVIDER": "cli", "ENVIRONMENT": "development",
                          "LLM_CLI_COMMAND": "codex exec", "LLM_CLI_MODEL": "some-model"})
    result = build_router(cfg, cli_runner=run, board=CooldownBoard()).generate(MSGS, Answer)
    assert result.value.word == "cli"
    argv, kwargs = run.calls[0]
    assert argv == ["codex", "exec", "--model", "some-model"]
    assert "rules one" in kwargs["input"] and "again" in kwargs["input"]
    assert kwargs["timeout"] > 0


@pytest.mark.parametrize("run,kind", [
    (FakeRun(returncode=1), "server"),
    (FakeRun(exc=subprocess.TimeoutExpired("claude", 1)), "timeout"),
    (FakeRun(exc=FileNotFoundError()), "bad_request"),
    (FakeRun(stdout="  "), "bad_response"),
])
def test_cli_failures_map_to_kinds(run, kind):
    adapter = CLIAdapter("claude --print", environment="development", runner=run)
    with pytest.raises(ProviderError) as exc:
        adapter.complete(MSGS, model="", json_schema=None, max_tokens=10, timeout=5)
    assert exc.value.kind == kind
