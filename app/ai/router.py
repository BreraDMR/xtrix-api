"""The one door to the models: generate(messages, schema, role) -> validated object.

Slots are tried in order. A slot that hits a rate limit or quota rests (see cooldown.py) and the
request moves on to the next one. A reply that isn't valid JSON for the schema gets one retry on the
same slot with a short note about what was wrong, then the next slot is the fallback.
Nothing in here ever raises anything but AIError subclasses.
"""
import json
import math
import re
import time
from dataclasses import dataclass
from typing import Generic, Literal, Optional, Sequence, Type, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.ai.config import AIConfig, SlotConfig, load_ai_config
from app.ai.cooldown import CooldownBoard, next_utc_midnight
from app.ai.errors import AIBusy, AINotConfigured, AIUnavailable
from app.ai.providers.base import Message, ModelAdapter, ProviderError
from app.api.core.logger import get_logger

logger = get_logger("ai")
T = TypeVar("T", bound=BaseModel)
Role = Literal["fast", "smart"]
# the bad reply echoed back on retry, so the model sees what to fix - capped, it costs tokens
ECHO_LIMIT = 4000


@dataclass
class Slot:
    config: SlotConfig
    adapter: ModelAdapter

    @property
    def label(self) -> str:
        return self.config.label

    def model_for(self, role: Role) -> str:
        return self.config.model_smart if role == "smart" else self.config.model_fast


@dataclass
class GenerateResult(Generic[T]):
    value: T
    slot_index: Optional[int]
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_estimate: float
    attempts: int


class InvalidReply(ValueError):
    pass


_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.S)


def parse_reply(text: str, schema: Type[T]) -> T:
    raw = text.strip()
    fenced = _FENCE.match(raw)
    if fenced:
        raw = fenced.group(1)
    try:
        obj = json.loads(raw)
    except ValueError:
        # some models chat a bit around the object - take the outermost braces
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise InvalidReply("the reply is not JSON") from None
        try:
            obj = json.loads(raw[start:end + 1])
        except ValueError:
            raise InvalidReply("the reply is not JSON") from None
    if not isinstance(obj, dict):
        raise InvalidReply("the reply must be one JSON object")
    try:
        # through JSON again so strict models read dates and datetimes from ISO strings
        return schema.model_validate_json(json.dumps(obj))
    except ValidationError as exc:
        # field paths and error types only - the values may be personal data
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['type']}" for e in exc.errors()[:8]
        )
        raise InvalidReply(f"the JSON does not match the schema ({problems})") from None


class LLMRouter:
    def __init__(self, slots: Sequence[Slot], *, config: Optional[AIConfig] = None,
                 board: Optional[CooldownBoard] = None):
        self.slots = list(slots)
        self.config = config or AIConfig()
        self.board = board or CooldownBoard()

    def _rest(self, slot: Slot, err: ProviderError) -> float:
        cfg = self.config
        key = slot.label
        if err.kind == "rate_limited":
            return self.board.cool(key, seconds=err.retry_after or cfg.cooldown_seconds)
        if err.kind == "quota":
            if err.retry_after:
                return self.board.cool(key, seconds=err.retry_after)
            if err.daily:
                return self.board.cool(key, until=next_utc_midnight(self.board.clock()))
            return self.board.cool(key, seconds=cfg.cooldown_seconds)
        if err.kind in ("server", "timeout", "network"):
            return self.board.cool(key, seconds=err.retry_after or cfg.server_error_cooldown_seconds)
        if err.kind == "auth":
            return self.board.cool(key, seconds=cfg.auth_error_cooldown_seconds)
        if err.kind == "bad_request":
            # wrong model id, or a provider that refuses our data policy - don't hammer it
            return self.board.cool(key, seconds=cfg.bad_request_cooldown_seconds)
        return 0.0

    def generate(self, messages: Sequence[Message], schema: Type[T], role: Role = "fast") -> GenerateResult[T]:
        if not self.slots:
            raise AINotConfigured("no model slot is configured")
        json_schema = schema.model_json_schema()
        prompt_tokens = completion_tokens = attempts = 0
        cost = 0.0
        waits: list[float] = []
        size = sum(len(m.content) for m in messages)
        for slot in self.slots:
            wait = self.board.remaining(slot.label)
            if wait > 0:
                waits.append(wait)
                continue
            model = slot.model_for(role)
            convo = list(messages)
            for attempt in (1, 2):
                attempts += 1
                started = time.monotonic()
                try:
                    reply = slot.adapter.complete(convo, model=model, json_schema=json_schema,
                                                  max_tokens=self.config.max_tokens,
                                                  timeout=self.config.timeout_seconds)
                except ProviderError as err:
                    rest = self._rest(slot, err)
                    if rest > 0:
                        waits.append(rest)
                    logger.warning("ai slot failed: slot=%s provider=%s model=%s kind=%s status=%s rest=%ss",
                                   slot.label, slot.config.name, model, err.kind, err.status, int(rest))
                    break
                except Exception as exc:  # a bug in an adapter must not take the request down
                    logger.error("ai slot crashed: slot=%s error=%s", slot.label, type(exc).__name__)
                    break
                ms = int((time.monotonic() - started) * 1000)
                prompt_tokens += reply.prompt_tokens
                completion_tokens += reply.completion_tokens
                cost += (reply.prompt_tokens * slot.config.price_in
                         + reply.completion_tokens * slot.config.price_out) / 1_000_000
                try:
                    value = parse_reply(reply.text, schema)
                except InvalidReply as bad:
                    logger.warning("ai invalid reply: slot=%s model=%s attempt=%s chars=%s ms=%s",
                                   slot.label, model, attempt, len(reply.text), ms)
                    convo = list(messages) + [
                        Message("assistant", reply.text[:ECHO_LIMIT]),
                        Message("user", f"That reply was rejected: {bad}. Answer again with only one JSON "
                                        "object that matches the schema from the instructions. No other text."),
                    ]
                    continue
                logger.info("ai ok: slot=%s provider=%s model=%s attempt=%s in_chars=%s tokens=%s/%s ms=%s",
                            slot.label, slot.config.name, model, attempt, size,
                            reply.prompt_tokens, reply.completion_tokens, ms)
                return GenerateResult(value, slot.config.index, slot.config.name, model,
                                      prompt_tokens, completion_tokens, round(cost, 6), attempts)
        tokens = dict(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
        if waits:
            raise AIBusy(retry_after=max(1, math.ceil(min(waits))), **tokens)
        raise AIUnavailable("no slot gave a valid answer", **tokens)


def _adapter_for(cfg: SlotConfig, http: Optional[httpx.Client]) -> ModelAdapter:
    if cfg.kind == "anthropic":
        from app.ai.providers.anthropic import AnthropicAdapter
        return AnthropicAdapter(cfg.api_key or "", base_url=cfg.base_url, http=http)
    from app.ai.providers.openai_compat import OpenAICompatAdapter
    return OpenAICompatAdapter(cfg.base_url, cfg.api_key, json_mode=cfg.json_mode, http=http)


def build_router(config: Optional[AIConfig] = None, *, http: Optional[httpx.Client] = None,
                 board: Optional[CooldownBoard] = None, cli_runner=None) -> LLMRouter:
    """Router for whatever LLM_PROVIDER says. http/cli_runner are for tests."""
    config = config or load_ai_config()
    if config.provider == "cli":
        from app.ai.providers.cli import CLIAdapter
        adapter = CLIAdapter(config.cli_command, environment=config.environment, runner=cli_runner)
        slot_cfg = SlotConfig(index=1, kind="cli", base_url="", api_key=None, model_fast=config.cli_model,
                              model_smart=config.cli_model, name="cli")
        return LLMRouter([Slot(slot_cfg, adapter)], config=config, board=board)
    slots = [Slot(cfg, _adapter_for(cfg, http)) for cfg in config.slots]
    return LLMRouter(slots, config=config, board=board)


_default_router: Optional[LLMRouter] = None


def get_router() -> LLMRouter:
    """The process-wide router (one cooldown board per worker)."""
    global _default_router
    if _default_router is None:
        _default_router = build_router()
    return _default_router
