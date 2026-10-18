"""The AI part of .env.example: ten slots at ten different providers, no keys committed."""
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values

from app.ai.config import load_ai_config

EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"


def test_ten_providers_prefilled_without_keys():
    env = dotenv_values(EXAMPLE)
    hosts = [urlparse(env[f"LLM_{i}_BASE_URL"]).hostname for i in range(1, 11)]
    assert len(set(hosts)) == 10
    assert all(env[f"LLM_{i}_MODEL"] for i in range(1, 11))
    assert all(not env[f"LLM_{i}_API_KEY"] for i in range(1, 11))
    assert not env["ANTHROPIC_API_KEY"]
    # with empty keys nothing is enabled
    assert load_ai_config(env).slots == ()


def test_filling_the_keys_gives_ten_slots():
    env = dotenv_values(EXAMPLE)
    env.update({f"LLM_{i}_API_KEY": f"test-{i}" for i in range(1, 11)})
    cfg = load_ai_config(env)
    assert [s.index for s in cfg.slots] == list(range(1, 11))
    assert len({s.name for s in cfg.slots}) == 10


def test_speech_slots_prefilled_without_keys():
    from app.ai.stt import load_stt_config

    env = dotenv_values(EXAMPLE)
    hosts = [urlparse(env[f"STT_{i}_BASE_URL"]).hostname for i in range(1, 4)]
    assert len(set(hosts)) == 3
    assert all(env[f"STT_{i}_MODEL"] for i in range(1, 4))
    assert all(not env[f"STT_{i}_API_KEY"] for i in range(1, 4))
    assert load_stt_config(env).ai.slots == ()
    env.update({f"STT_{i}_API_KEY": f"test-{i}" for i in range(1, 4)})
    assert [s.index for s in load_stt_config(env).ai.slots] == [1, 2, 3]


def test_fake_provider_documented_but_off():
    env = dotenv_values(EXAMPLE)
    assert env["LLM_PROVIDER"] == "pool" and "LLM_FAKE_PLAN_FILE" in env
    assert env.get("STT_PROVIDER") == "pool" and "STT_FAKE_TEXT" in env
