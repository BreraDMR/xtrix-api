"""Model settings from the environment.

Kept apart from app settings because the slot list is open-ended (LLM_1_* ... LLM_N_*), and so
tests can build a config from a plain dict without touching the process env.
"""
import os
from dataclasses import dataclass, field
from typing import Mapping, Optional
from urllib.parse import urlparse

from dotenv import dotenv_values

# Damir's start: 10 free keys. Scanning a bit further means more slots are an env change only.
MAX_SLOTS = 30
KEYLESS = "none"
PROVIDERS = ("pool", "anthropic", "cli", "fake")
JSON_MODES = ("json_object", "json_schema", "none")

# requests per calendar month (UTC) by subscription plan code
DEFAULT_MONTHLY_LIMITS = {
    "trial_admin": 50,
    "trial_specialist": 50,
    "solo_basic": 100,
    "solo_pro": 300,
    "admin_up_to_3_employees": 300,
    "admin_up_to_7_employees": 600,
    "admin_up_to_15_employees": 1200,
    "admin_unlimited_monthly": 2000,
    "admin_unlimited_yearly": 2000,
}


@dataclass(frozen=True)
class SlotConfig:
    index: int
    kind: str  # "openai" (any OpenAI-compatible API) or "anthropic"
    base_url: str
    api_key: Optional[str]  # None = the endpoint needs no key (local Ollama, anonymous tiers)
    model_fast: str
    model_smart: str
    name: str  # provider label for logs and usage rows - never the key
    json_mode: str = "json_object"
    price_in: float = 0.0  # USD per 1M tokens, for the cost estimate
    price_out: float = 0.0

    @property
    def label(self) -> str:
        return f"slot{self.index}"


@dataclass(frozen=True)
class AIConfig:
    provider: str = "pool"
    environment: str = "development"
    timeout_seconds: float = 30.0
    max_tokens: int = 4000
    cooldown_seconds: int = 60
    server_error_cooldown_seconds: int = 30
    auth_error_cooldown_seconds: int = 3600
    bad_request_cooldown_seconds: int = 600
    slots: tuple = ()
    cli_command: str = "claude --print"
    cli_model: str = ""
    fake_plan_file: str = ""
    default_timezone: str = "Europe/Prague"
    monthly_limit_default: int = 100
    monthly_limits: dict = field(default_factory=lambda: dict(DEFAULT_MONTHLY_LIMITS))


def _env() -> dict:
    # .env first, the real environment wins - same order as pydantic-settings
    merged = {k.upper(): v for k, v in dotenv_values(".env").items() if v is not None}
    merged.update({k.upper(): v for k, v in os.environ.items()})
    return merged


def _get(env: Mapping[str, str], key: str, default: str = "") -> str:
    return (env.get(key) or default).strip()


def _num(env, key, default, cast=float):
    raw = _get(env, key)
    if not raw:
        return default
    try:
        return cast(raw)
    except ValueError:
        raise ValueError(f"{key} must be a number") from None


def _provider_name(base_url: str) -> str:
    host = urlparse(base_url).hostname or "unknown"
    for known in ("openrouter", "googleapis", "groq", "cerebras", "mistral", "cohere", "nvidia",
                  "cloudflare", "z.ai", "ovh", "anthropic", "localhost", "127.0.0.1"):
        if known in host:
            return {"googleapis": "google", "z.ai": "zai", "localhost": "local", "127.0.0.1": "local"}.get(known, known)
    return host[:64]


def _pool_slots(env: Mapping[str, str]) -> list[SlotConfig]:
    slots = []
    for i in range(1, MAX_SLOTS + 1):
        p = f"LLM_{i}_"
        base_url, model, key = _get(env, p + "BASE_URL"), _get(env, p + "MODEL"), _get(env, p + "API_KEY")
        if not base_url or not model or not key:
            # an empty key means "not set up yet", not "no key needed" - that one is spelled out
            continue
        kind = _get(env, p + "KIND", "openai").lower()
        if kind not in ("openai", "anthropic"):
            raise ValueError(f"{p}KIND must be openai or anthropic")
        json_mode = _get(env, p + "JSON_MODE", "json_object").lower()
        if json_mode not in JSON_MODES:
            raise ValueError(f"{p}JSON_MODE must be one of {', '.join(JSON_MODES)}")
        slots.append(SlotConfig(
            index=i,
            kind=kind,
            base_url=base_url.rstrip("/"),
            api_key=None if key.lower() == KEYLESS else key,
            model_fast=model,
            model_smart=_get(env, p + "MODEL_SMART") or model,
            name=_get(env, p + "NAME") or _provider_name(base_url),
            json_mode=json_mode,
            price_in=_num(env, p + "PRICE_IN", 0.0),
            price_out=_num(env, p + "PRICE_OUT", 0.0),
        ))
    return slots


def _anthropic_slots(env: Mapping[str, str]) -> list[SlotConfig]:
    key = _get(env, "ANTHROPIC_API_KEY")
    if not key:
        return []
    base_url = _get(env, "ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
    fast = _get(env, "ANTHROPIC_MODEL_FAST", "claude-haiku-5-5")
    smart = _get(env, "ANTHROPIC_MODEL_SMART") or fast
    common = dict(kind="anthropic", base_url=base_url, api_key=key, name="anthropic",
                  price_in=_num(env, "ANTHROPIC_PRICE_IN", 0.0), price_out=_num(env, "ANTHROPIC_PRICE_OUT", 0.0))
    slots = [SlotConfig(index=1, model_fast=fast, model_smart=smart, **common)]
    fallback = _get(env, "ANTHROPIC_MODEL_FALLBACK")
    if fallback:
        # the fallback model rides the same key as a second slot, so it gets the same rotation rules
        slots.append(SlotConfig(index=2, model_fast=fallback, model_smart=fallback, **common))
    return slots


def _limits(env: Mapping[str, str]) -> dict:
    limits = dict(DEFAULT_MONTHLY_LIMITS)
    raw = _get(env, "AI_MONTHLY_LIMITS")
    for part in filter(None, (p.strip() for p in raw.split(","))):
        code, _, value = part.partition("=")
        try:
            limits[code.strip()] = int(value)
        except ValueError:
            raise ValueError("AI_MONTHLY_LIMITS looks like plan_code=100,other_code=300") from None
    return limits


def load_ai_config(env: Optional[Mapping[str, str]] = None) -> AIConfig:
    env = {k.upper(): v for k, v in (env if env is not None else _env()).items()}
    provider = _get(env, "LLM_PROVIDER", "pool").lower()
    if provider == "openai":
        provider = "pool"
    if provider not in PROVIDERS:
        raise ValueError(f"LLM_PROVIDER must be one of {', '.join(PROVIDERS)}")
    slots = _anthropic_slots(env) if provider == "anthropic" else _pool_slots(env) if provider == "pool" else []
    return AIConfig(
        provider=provider,
        environment=_get(env, "ENVIRONMENT", "development").lower(),
        timeout_seconds=_num(env, "LLM_TIMEOUT_SECONDS", 30.0),
        max_tokens=_num(env, "LLM_MAX_TOKENS", 4000, int),
        cooldown_seconds=_num(env, "LLM_COOLDOWN_SECONDS", 60, int),
        slots=tuple(slots),
        cli_command=_get(env, "LLM_CLI_COMMAND", "claude --print"),
        cli_model=_get(env, "LLM_CLI_MODEL"),
        fake_plan_file=_get(env, "LLM_FAKE_PLAN_FILE"),
        default_timezone=_get(env, "AI_DEFAULT_TIMEZONE", "Europe/Prague"),
        monthly_limit_default=_num(env, "AI_MONTHLY_LIMIT_DEFAULT", 100, int),
        monthly_limits=_limits(env),
    )
