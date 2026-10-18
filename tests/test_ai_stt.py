"""Speech to text (В): a small slot pool like the model one, fake providers only."""
import logging

import httpx
import pytest

from app.ai import stt
from app.ai.config import load_ai_config
from app.ai.cooldown import CooldownBoard
from app.ai.errors import AIBusy, AILimitReached, AINotConfigured, AIUnavailable
from app.api.crud import crud_user
from app.db.models.ai_usage import AIUsage
from tests.tenant_world import build_tenant

WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 200
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 100
OGG = b"OggS" + b"\x00" * 100
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100
MP3 = b"ID3\x04" + b"\x00" * 100

ENV = {"STT_1_BASE_URL": "https://api.groq.com/openai/v1", "STT_1_API_KEY": "k1", "STT_1_MODEL": "whisper-x",
       "STT_2_BASE_URL": "https://api.openai.com/v1", "STT_2_API_KEY": "k2", "STT_2_MODEL": "gpt-transcribe",
       "STT_3_BASE_URL": "http://localhost:8001/v1", "STT_3_API_KEY": "", "STT_3_MODEL": "local"}


class Provider:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        if not self.answers:
            return httpx.Response(200, json={"text": "  Book Jana tomorrow at ten  "})
        return self.answers.pop(0)


def router_for(provider, env=ENV):
    return stt.build_stt_router(stt.load_stt_config(env), http=httpx.Client(transport=httpx.MockTransport(provider)),
                                board=CooldownBoard())


# --- input checks ------------------------------------------------------------

@pytest.mark.parametrize("ctype,data,ext", [
    ("audio/webm", WEBM, "webm"), ("audio/webm;codecs=opus", WEBM, "webm"), ("audio/ogg", OGG, "ogg"),
    ("audio/mp4", MP4, "mp4"), ("audio/mpeg", MP3, "mp3"), ("audio/wav", WAV, "wav"), ("audio/x-wav", WAV, "wav"),
])
def test_accepted_audio(ctype, data, ext):
    assert stt.check_audio(data, ctype) == ext


@pytest.mark.parametrize("ctype,data,code", [
    ("text/plain", b"hello", "audio_type_not_supported"),
    ("video/webm", WEBM, "audio_type_not_supported"),
    (None, WEBM, "audio_type_not_supported"),
    ("audio/webm", b"", "audio_empty"),
    ("audio/webm", b"<html>not audio</html>", "audio_type_not_supported"),
    ("audio/webm", WEBM[:4] + b"\x00" * stt.MAX_AUDIO_BYTES, "audio_too_large"),
])
def test_rejected_audio(ctype, data, code):
    with pytest.raises(stt.AudioRejected) as exc:
        stt.check_audio(data, ctype)
    assert exc.value.code == code


def test_exactly_five_megabytes_is_fine():
    data = WEBM[:4] + b"\x00" * (stt.MAX_AUDIO_BYTES - 4)
    assert stt.check_audio(data, "audio/webm") == "webm"


# --- config ------------------------------------------------------------------

def test_empty_key_is_skipped_and_none_means_keyless():
    cfg = stt.load_stt_config(ENV)
    assert [s.index for s in cfg.ai.slots] == [1, 2]
    cfg = stt.load_stt_config({**ENV, "STT_3_API_KEY": "none"})
    assert [(s.index, s.api_key, s.name) for s in cfg.ai.slots][2] == (3, None, "local")
    assert stt.load_stt_config({}).ai.slots == ()
    with pytest.raises(ValueError):
        stt.load_stt_config({"STT_PROVIDER": "whatever"})


# --- the pool ----------------------------------------------------------------

def test_request_shape_and_text():
    provider = Provider()
    result = router_for(provider).transcribe(WEBM, ext="webm", content_type="audio/webm", language="cs")
    assert (result.text, result.slot_index, result.provider, result.model) == (
        "Book Jana tomorrow at ten", 1, "groq", "whisper-x")
    [req] = provider.requests
    assert str(req.url) == "https://api.groq.com/openai/v1/audio/transcriptions"
    assert req.headers["authorization"] == "Bearer k1"
    body = req.content
    assert b'name="model"' in body and b"whisper-x" in body
    assert b'name="language"' in body and b"cs" in body
    assert b'filename="audio.webm"' in body and WEBM in body


def test_rate_limited_slot_rests_and_the_next_one_answers():
    provider = Provider(httpx.Response(429, headers={"Retry-After": "20"}))
    router = router_for(provider)
    result = router.transcribe(WEBM, ext="webm", content_type="audio/webm", language=None)
    assert result.slot_index == 2
    assert 19 < router.board.remaining("slot1") <= 20
    # the resting slot is skipped next time
    provider.requests.clear()
    router.transcribe(WEBM, ext="webm", content_type="audio/webm", language=None)
    assert [str(r.url).split("/")[2] for r in provider.requests] == ["api.openai.com"]


def test_all_slots_resting_is_busy_with_retry_after():
    limited = httpx.Response(429, headers={"Retry-After": "30"})
    router = router_for(Provider(limited, httpx.Response(429, headers={"Retry-After": "12"})))
    with pytest.raises(AIBusy) as exc:
        router.transcribe(WEBM, ext="webm", content_type="audio/webm", language=None)
    assert exc.value.retry_after == 12


def test_broken_answers_are_unavailable():
    router = router_for(Provider(httpx.Response(200, json={"nope": 1}), httpx.Response(200, text="oops")))
    with pytest.raises(AIUnavailable):
        router.transcribe(WEBM, ext="webm", content_type="audio/webm", language=None)


def test_nothing_configured():
    with pytest.raises(AINotConfigured):
        router_for(Provider(), env={}).transcribe(WEBM, ext="webm", content_type="audio/webm", language=None)


def test_fake_stt_never_in_production():
    router = stt.build_stt_router(stt.load_stt_config({"STT_PROVIDER": "fake", "STT_FAKE_TEXT": "hello"}))
    assert router.transcribe(WEBM, ext="webm", content_type="audio/webm", language=None).text == "hello"
    with pytest.raises(AINotConfigured):
        stt.build_stt_router(stt.load_stt_config({"STT_PROVIDER": "fake", "ENVIRONMENT": "production"}))


# --- transcribe(): limit and usage -------------------------------------------

@pytest.fixture
def owner(client, make_user, headers_for, db):
    t = build_tenant(client, make_user, headers_for, "Beta", "+42070022")
    db.expire_all()
    return crud_user.get_active_user(db, t["owner"].id)


def test_transcribe_counts_one_request_as_stt(db, owner):
    text = stt.transcribe(db, owner, WEBM, "audio/webm", locale="ru", router=router_for(Provider()),
                          config=load_ai_config({}))
    assert text == "Book Jana tomorrow at ten"
    rows = db.query(AIUsage).all()
    assert [(r.role, r.provider, r.slot_index, r.model, r.succeeded, r.user_id) for r in rows] == [
        ("stt", "groq", 1, "whisper-x", True, owner.id)]


def test_transcribe_respects_the_monthly_limit(db, owner):
    cfg = load_ai_config({"AI_MONTHLY_LIMITS": "trial_admin=1"})
    provider = Provider()
    stt.transcribe(db, owner, WEBM, "audio/webm", locale="en", router=router_for(provider), config=cfg)
    with pytest.raises(AILimitReached):
        stt.transcribe(db, owner, WEBM, "audio/webm", locale="en", router=router_for(provider), config=cfg)
    assert len(provider.requests) == 1


def test_failed_transcription_costs_nothing(db, owner):
    busy = httpx.Response(429, headers={"Retry-After": "5"})
    with pytest.raises(AIBusy):
        stt.transcribe(db, owner, WEBM, "audio/webm", locale="en", router=router_for(Provider(busy, busy)),
                       config=load_ai_config({}))
    db.expire_all()
    assert db.query(AIUsage).count() == 0


def test_bad_audio_is_refused_before_the_limit_or_a_provider(db, owner):
    provider = Provider()
    with pytest.raises(stt.AudioRejected):
        stt.transcribe(db, owner, b"plain text", "audio/webm", locale="en", router=router_for(provider),
                       config=load_ai_config({}))
    assert provider.requests == [] and db.query(AIUsage).count() == 0


def test_neither_audio_nor_text_is_logged(db, owner, caplog):
    logger = logging.getLogger("xtrix")
    logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.DEBUG, logger="xtrix"):
            stt.transcribe(db, owner, WEBM, "audio/webm", locale="en", router=router_for(Provider()),
                           config=load_ai_config({}))
    finally:
        logger.removeHandler(caplog.handler)
    assert "stt" in caplog.text
    assert "Jana" not in caplog.text and "tomorrow" not in caplog.text
