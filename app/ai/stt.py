"""Speech to text for the assistant (В): audio in, text out, nothing kept.

Same idea as the model pool: up to three OpenAI-compatible slots (STT_1_* .. STT_3_*), tried in order,
a slot that hits a limit rests and the next one answers, all resting -> AIBusy. Every provider here
speaks POST {base}/audio/transcriptions (multipart: file, model).

The audio and the text are never stored or logged - only sizes, slot and status. The text goes back
to the user to read and edit; it is not planned automatically.
"""
import math
import time
from dataclasses import dataclass
from typing import Mapping, Optional

import httpx
from sqlalchemy.orm import Session

from app.ai.config import AIConfig, KEYLESS, SlotConfig, _env, _get, _num, _provider_name, load_ai_config
from app.ai.errors import AIBusy, AIError, AINotConfigured, AIUnavailable
from app.ai.providers.base import ProviderError, error_from_response
from app.ai.router import LLMRouter, Slot
from app.ai.usage import release, reserve_request, settle
from app.api.core.logger import get_logger
from app.db.models.user import User

logger = get_logger("ai.stt")
MAX_STT_SLOTS = 3
MAX_AUDIO_BYTES = 5 * 1024 * 1024
STT_PROVIDERS = ("pool", "fake")
# what the browser recorders send; the x-wav/wave spellings are the same thing
AUDIO_TYPES = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "mp4", "audio/mpeg": "mp3",
               "audio/wav": "wav", "audio/x-wav": "wav", "audio/wave": "wav"}
# ISO 639-1 hint for the recogniser; our "cz" is "cs" there
LANGUAGE_HINTS = {"ru": "ru", "cz": "cs", "en": "en"}


class AudioRejected(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _looks_like(ext: str, data: bytes) -> bool:
    # a cheap look at the first bytes, so a renamed text file doesn't get shipped to a provider
    if ext == "webm":
        return data[:4] == b"\x1a\x45\xdf\xa3"
    if ext == "ogg":
        return data[:4] == b"OggS"
    if ext == "wav":
        return data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    if ext == "mp4":
        return data[4:8] == b"ftyp"
    if ext == "mp3":
        return data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0)
    return False


def too_large() -> AudioRejected:
    return AudioRejected("audio_too_large", f"the recording is larger than {MAX_AUDIO_BYTES // (1024 * 1024)} MB")


def check_audio(data: bytes, content_type: Optional[str]) -> str:
    """The file extension to send it as, or AudioRejected."""
    base = (content_type or "").split(";")[0].strip().lower()
    ext = AUDIO_TYPES.get(base)
    if ext is None:
        raise AudioRejected("audio_type_not_supported", "send audio/webm, audio/ogg, audio/mp4, audio/mpeg or audio/wav")
    if not data:
        raise AudioRejected("audio_empty", "the recording is empty")
    if len(data) > MAX_AUDIO_BYTES:
        raise too_large()
    if not _looks_like(ext, data):
        raise AudioRejected("audio_type_not_supported", "the file is not the audio type it says it is")
    return ext


# --- config ------------------------------------------------------------------

def _stt_slots(env: Mapping[str, str]) -> list[SlotConfig]:
    slots = []
    for i in range(1, MAX_STT_SLOTS + 1):
        p = f"STT_{i}_"
        base_url, model, key = _get(env, p + "BASE_URL"), _get(env, p + "MODEL"), _get(env, p + "API_KEY")
        if not base_url or not model or not key:
            continue  # empty key = not set up yet; a keyless local server says "none"
        slots.append(SlotConfig(index=i, kind="stt", base_url=base_url.rstrip("/"),
                                api_key=None if key.lower() == KEYLESS else key,
                                model_fast=model, model_smart=model,
                                name=_get(env, p + "NAME") or _provider_name(base_url)))
    return slots


@dataclass(frozen=True)
class SttConfig:
    ai: AIConfig  # provider, environment, timeout, cooldowns and slots, in the router's own terms
    fake_text: str = ""


def load_stt_config(env: Optional[Mapping[str, str]] = None) -> SttConfig:
    env = {k.upper(): v for k, v in (env if env is not None else _env()).items()}
    provider = _get(env, "STT_PROVIDER", "pool").lower()
    if provider not in STT_PROVIDERS:
        raise ValueError(f"STT_PROVIDER must be one of {', '.join(STT_PROVIDERS)}")
    ai = AIConfig(
        provider=provider,
        environment=_get(env, "ENVIRONMENT", "development").lower(),
        timeout_seconds=_num(env, "STT_TIMEOUT_SECONDS", 60.0),
        cooldown_seconds=_num(env, "LLM_COOLDOWN_SECONDS", 60, int),
        slots=tuple(_stt_slots(env)) if provider == "pool" else (),
    )
    return SttConfig(ai=ai, fake_text=_get(env, "STT_FAKE_TEXT"))


# --- adapters ----------------------------------------------------------------

class OpenAICompatSttAdapter:
    def __init__(self, base_url: str, api_key: Optional[str], *, http: Optional[httpx.Client] = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.http = http or httpx.Client()

    def transcribe(self, audio: bytes, *, filename: str, content_type: str, model: str,
                   language: Optional[str], timeout: float) -> str:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        data = {"model": model, "response_format": "json"}
        if language:
            data["language"] = language
        try:
            response = self.http.post(f"{self.base_url}/audio/transcriptions", headers=headers, data=data,
                                      files={"file": (filename, audio, content_type)}, timeout=timeout)
        except httpx.TimeoutException:
            raise ProviderError("timeout", detail="no answer in time") from None
        except httpx.HTTPError as exc:
            raise ProviderError("network", detail=type(exc).__name__) from None
        if response.status_code >= 400:
            raise error_from_response(response)
        try:
            body = response.json()
        except ValueError:
            raise ProviderError("bad_response", status=response.status_code, detail="body is not JSON") from None
        text = body.get("text") if isinstance(body, dict) else None
        if not isinstance(text, str):
            raise ProviderError("bad_response", status=response.status_code, detail="no text in the answer")
        return text


class FakeSttAdapter:
    """STT_PROVIDER=fake: always "hears" STT_FAKE_TEXT. Tests and E2E only."""

    def __init__(self, text: str, *, environment: str):
        if environment == "production":
            raise AINotConfigured("the fake speech provider is for development and tests only")
        self.text = text or "fake transcription"

    def transcribe(self, audio: bytes, **kwargs) -> str:
        return self.text


# --- router ------------------------------------------------------------------

@dataclass
class SttResult:
    text: str
    slot_index: Optional[int]
    provider: str
    model: str


class SttRouter(LLMRouter):
    """The LLM router's slot rotation and rest rules, with transcribe instead of generate."""

    def transcribe(self, audio: bytes, *, ext: str, content_type: str, language: Optional[str]) -> SttResult:
        if not self.slots:
            raise AINotConfigured("no speech slot is configured")
        waits: list[float] = []
        for slot in self.slots:
            wait = self.board.remaining(slot.label)
            if wait > 0:
                waits.append(wait)
                continue
            model = slot.model_for("fast")
            started = time.monotonic()
            try:
                text = slot.adapter.transcribe(audio, filename=f"audio.{ext}", content_type=content_type,
                                               model=model, language=language,
                                               timeout=self.config.timeout_seconds)
            except ProviderError as err:
                rest = self._rest(slot, err)
                if rest > 0:
                    waits.append(rest)
                logger.warning("stt slot failed: slot=%s provider=%s kind=%s status=%s rest=%ss",
                               slot.label, slot.config.name, err.kind, err.status, int(rest))
                continue
            except Exception as exc:
                logger.error("stt slot crashed: slot=%s error=%s", slot.label, type(exc).__name__)
                continue
            logger.info("stt ok: slot=%s provider=%s bytes=%s ms=%s", slot.label, slot.config.name, len(audio),
                        int((time.monotonic() - started) * 1000))
            return SttResult(text.strip(), slot.config.index, slot.config.name, model)
        if waits:
            raise AIBusy(retry_after=max(1, math.ceil(min(waits))))
        raise AIUnavailable("no speech slot gave an answer")


def build_stt_router(config: Optional[SttConfig] = None, *, http: Optional[httpx.Client] = None,
                     board=None) -> SttRouter:
    config = config or load_stt_config()
    if config.ai.provider == "fake":
        adapter = FakeSttAdapter(config.fake_text, environment=config.ai.environment)
        slot_cfg = SlotConfig(index=1, kind="stt", base_url="", api_key=None, model_fast="fake",
                              model_smart="fake", name="fake")
        return SttRouter([Slot(slot_cfg, adapter)], config=config.ai, board=board)
    slots = [Slot(cfg, OpenAICompatSttAdapter(cfg.base_url, cfg.api_key, http=http)) for cfg in config.ai.slots]
    return SttRouter(slots, config=config.ai, board=board)


_default_stt_router: Optional[SttRouter] = None


def get_stt_router() -> SttRouter:
    global _default_stt_router
    if _default_stt_router is None:
        from app.ai.shared import SharedCooldownBoard
        _default_stt_router = build_stt_router(board=SharedCooldownBoard("stt"))
    return _default_stt_router


def transcribe(db: Session, user: User, audio: bytes, content_type: Optional[str], *, locale: str,
               router: Optional[SttRouter] = None, config: Optional[AIConfig] = None) -> str:
    """Checks the audio, takes one request from the monthly limit, returns the text."""
    ext = check_audio(audio, content_type)
    router = router or get_stt_router()
    if not router.slots:
        raise AINotConfigured("no speech slot is configured")
    usage = reserve_request(db, user, config or load_ai_config(), role="stt")
    try:
        result = router.transcribe(audio, ext=ext, content_type=(content_type or "").split(";")[0].strip().lower(),
                                   language=LANGUAGE_HINTS.get(locale))
    except AIError as err:
        release(db, usage)
        logger.warning("stt failed: user=%s code=%s bytes=%s", user.id, err.code, len(audio))
        raise
    except Exception:
        release(db, usage)
        raise
    settle(db, usage, provider=result.provider, slot_index=result.slot_index, model=result.model,
           prompt_tokens=0, completion_tokens=0, cost_estimate=0.0, succeeded=True)
    logger.info("stt: user=%s bytes=%s chars=%s", user.id, len(audio), len(result.text))
    return result.text
