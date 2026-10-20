"""Unsafe test targets must be rejected before importing the app or touching Docker."""
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize('url', [
    'postgresql://user:secret@remote.example/xtrix_test',
    'postgresql://user:secret@localhost/xtrix_local',
    'postgresql://user:secret@localhost/contest',
    'postgresql://user:secret@localhost/xtrix_test?host=remote.example&dbname=live_database',
    'postgresql://user:secret@localhost/xtrix_test?hostaddr=192.0.2.1',
    'postgresql://user:secret@localhost/xtrix_test?service=live_database',
])
def test_check_rejects_unsafe_database_before_tools(url):
    result = subprocess.run(
        ['bash', 'scripts/check.sh'],
        env={**os.environ, 'DATABASE_URL': url, 'PYTHON': sys.executable},
        text=True, capture_output=True, timeout=15,
    )
    assert result.returncode != 0
    assert 'Refusing' in result.stderr
    assert '==>' not in result.stdout
    assert 'secret' not in result.stderr


@pytest.mark.parametrize('url', [
    'postgresql://user:secret@remote.example/xtrix_test',
    'postgresql://user:secret@localhost/contest',
    'postgresql://user:secret@localhost/xtrix_test?host=remote.example&dbname=live_database',
    'postgresql://user:secret@localhost/xtrix_test?hostaddr=192.0.2.1',
    'postgresql://user:secret@localhost/xtrix_test?service=live_database',
])
def test_pytest_guard_rejects_unsafe_database(url):
    result = subprocess.run(
        [sys.executable, '-c', "import runpy; runpy.run_path('tests/conftest.py')"],
        env={**os.environ, 'DATABASE_URL': url}, text=True, capture_output=True, timeout=15,
    )
    assert result.returncode != 0
    assert 'Refusing' in result.stderr
    assert 'secret' not in result.stderr


@pytest.mark.parametrize('key', ['PGHOSTADDR', 'PGSERVICE', 'PGSERVICEFILE'])
@pytest.mark.parametrize('command', [
    ['bash', 'scripts/check.sh'],
    [sys.executable, '-c', "import runpy; runpy.run_path('tests/conftest.py')"],
])
def test_guards_reject_libpq_routing_environment(key, command):
    result = subprocess.run(
        command,
        env={**os.environ, 'DATABASE_URL': 'postgresql://user:secret@localhost/xtrix_test',
             'PYTHON': sys.executable, key: 'remote-test-override'},
        text=True, capture_output=True, timeout=15,
    )
    assert result.returncode != 0
    assert 'Refusing' in result.stderr
    assert '==>' not in result.stdout
    assert 'secret' not in result.stderr


@pytest.mark.parametrize('create_fails', [False, True])
def test_check_cleans_only_owned_container_after_start_failure(tmp_path, create_fails):
    python = tmp_path / 'fake-python'
    python.write_text('#!/bin/sh\nexit 0\n')
    python.chmod(0o755)
    docker = tmp_path / 'docker'
    log = tmp_path / 'docker.log'
    docker.write_text(
        '#!/bin/sh\n'
        f'printf "%s\\n" "$*" >> "{log}"\n'
        'case "$1" in\n'
        + ('create) exit 1;;\n' if create_fails else 'create) echo owned-test-container;;\n')
        + 'start) exit 42;;\n'
        'rm) exit 0;;\n'
        '*) exit 99;;\n'
        'esac\n'
    )
    docker.chmod(0o755)
    env = {key: value for key, value in os.environ.items() if key != 'DATABASE_URL'}
    result = subprocess.run(
        ['bash', 'scripts/check.sh'],
        env={**env, 'PYTHON': str(python), 'PATH': f"{tmp_path}:{env['PATH']}"},
        text=True, capture_output=True, timeout=15,
    )
    assert result.returncode != 0
    calls = log.read_text().splitlines()
    removals = [call for call in calls if call.startswith('rm ')]
    assert removals == ([] if create_fails else ['rm -f owned-test-container'])
