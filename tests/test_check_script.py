"""check.sh must not let parallel runs share the compose Redis (analytics cache, rate limits)."""
import os
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check.sh"


def _redis_url_seen_by_check(tmp_path, env):
    # a fake python that records REDIS_URL and fails, so check.sh stops at its first step
    out = tmp_path / "redis_url.txt"
    fake = tmp_path / "fake-python"
    fake.write_text(f'#!/bin/sh\nprintf "%s" "${{REDIS_URL-unset}}" > "{out}"\nexit 1\n')
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    subprocess.run(["bash", str(SCRIPT)], env={**env, "PYTHON": str(fake)},
                   capture_output=True, timeout=30)
    return out.read_text()


def test_check_defaults_redis_to_an_unreachable_one(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "REDIS_URL"}
    url = _redis_url_seen_by_check(tmp_path, env)
    assert url.startswith("redis://localhost:1/"), url


def test_check_keeps_a_redis_url_given_from_outside(tmp_path):
    env = {**os.environ, "REDIS_URL": "redis://localhost:6390/3"}
    assert _redis_url_seen_by_check(tmp_path, env) == "redis://localhost:6390/3"
