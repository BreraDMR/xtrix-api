"""check.sh must not let parallel runs share the compose Redis (analytics cache, rate limits)."""
import os
import stat
import sys
import pytest
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


@pytest.mark.parametrize("url", ["redis://localhost:6390/3", "redis://cache.example.invalid:6379/0"])
def test_check_neutralizes_a_redis_url_given_from_outside(tmp_path, url):
    env = {**os.environ, "REDIS_URL": url}
    assert _redis_url_seen_by_check(tmp_path, env) == "redis://localhost:1/0"


def test_direct_pytest_setup_neutralizes_external_redis():
    # Importing conftest does not run its DB fixtures or connect to Redis.
    env = {**os.environ, "REDIS_URL": "redis://cache.example.invalid:6379/0"}
    result = subprocess.run([sys.executable, "-c", "import tests.conftest; from app.api.core.config import settings; print(settings.redis_url)"],
                            env=env, cwd=SCRIPT.parent.parent, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "redis://localhost:1/0"


@pytest.mark.parametrize("never_ready", [False, True])
def test_check_waits_for_final_tcp_server_and_refuses_timeout(tmp_path, never_ready):
    log = tmp_path / "docker.log"
    probes = tmp_path / "probes"
    ready = tmp_path / "ready"
    called = tmp_path / "pytest-called"
    fake_python = tmp_path / "fake-python"
    fake_python.write_text(
        '#!/bin/sh\n'
        'case "$*" in\n'
        f'  *"-m pytest"*) touch "{called}"; test -f "{ready}";;\n'
        '  *) exit 0;;\nesac\n')
    fake_python.chmod(0o755)
    docker = tmp_path / "docker"
    docker.write_text(
        '#!/bin/sh\n'
        f'printf "%s\\n" "$*" >> "{log}"\n'
        'case "$1" in\n'
        '  create) echo owned-readiness-container;;\n'
        '  start|rm) exit 0;;\n'
        '  exec)\n'
        + ('    exit 1;;\n' if never_ready else
           '    case "$*" in\n'
           '      *"pg_isready -h 127.0.0.1"*)\n'
           f'        n=0; test ! -f "{probes}" || n=$(cat "{probes}")\n'
           f'        n=$((n + 1)); echo "$n" > "{probes}"\n'
           f'        test "$n" -lt 2 || touch "{ready}"\n'
           f'        test -f "{ready}";;\n'
           '      *) exit 0;;\n'
           '    esac;;\n')
        + 'esac\n')
    docker.chmod(0o755)
    sleep = tmp_path / "sleep"
    sleep.write_text('#!/bin/sh\nexit 0\n')
    sleep.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL" and not k.startswith("PG")}
    env.update(PYTHON=str(fake_python), PATH=f"{tmp_path}:{env['PATH']}")
    result = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=10)
    calls = log.read_text().splitlines()
    assert [line for line in calls if line.startswith("rm ")] == ["rm -f owned-readiness-container"]
    if never_ready:
        assert result.returncode != 0
        assert not called.exists()
        assert "refusing to run pytest" in result.stderr
        assert len([line for line in calls if line.startswith("exec ")]) == 30
    else:
        assert result.returncode == 0, result.stderr
        assert called.exists() and ready.exists()
        assert probes.read_text().strip() == "2"
