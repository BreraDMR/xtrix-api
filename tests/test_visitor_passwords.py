"""Visitor password storage keeps the complete secret and has a bounded KDF."""
from concurrent.futures import ThreadPoolExecutor
import importlib
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, Lock

import bcrypt
import pytest


@pytest.fixture
def passwords():
    try:
        return importlib.import_module("app.api.core.visitor_passwords")
    except ImportError:
        pytest.fail("The isolated visitor password helper is not implemented yet")


@pytest.mark.parametrize("prefix", ["Aa1!Zp9@" * 9, "Ж" * 36])
def test_suffix_after_72_utf8_bytes_is_significant(passwords, prefix):
    assert len(prefix.encode("utf-8")) == 72
    left, right = prefix + "A", prefix + "B"
    encoded = passwords.hash_password(left)
    assert passwords.verify_password(left, encoded) is True
    assert passwords.verify_password(right, encoded) is False


def test_maximum_unicode_password_is_not_truncated(passwords):
    left, right = "🙂" * 127 + "A", "🙂" * 127 + "B"
    encoded = passwords.hash_password(left)
    assert passwords.verify_password(left, encoded)
    assert not passwords.verify_password(right, encoded)


def test_salts_are_independent_and_no_plain_secret_is_stored(passwords):
    raw = "Synthetic!Visitor-Password-26"
    first, second = passwords.hash_password(raw), passwords.hash_password(raw)
    assert first != second
    for encoded in (first, second):
        fields = encoded.split("$")
        assert fields[:6] == ["", "xtrix-visitor-scrypt", "v1", "32768", "8", "3"]
        assert len(bytes.fromhex(fields[6])) == 16
        assert len(bytes.fromhex(fields[7])) == 32
        assert len(encoded) <= 255 and raw not in encoded
        assert passwords.verify_password(raw, encoded)


@pytest.mark.parametrize("raw,other", [("  Полный пароль!  ", "Полный пароль!"),
    ("case-sensitive", "Case-sensitive"), ("before\0afterA", "before\0afterB"),
    ("e\u0301-composed", "é-composed")])
def test_password_bytes_are_not_trimmed_normalized_or_null_terminated(passwords, raw, other):
    encoded = passwords.hash_password(raw)
    assert passwords.verify_password(raw, encoded)
    assert not passwords.verify_password(other, encoded)


@pytest.mark.parametrize("invalid", [None, True, 42, b"bytes", "", "x" * 129, "\ud800"])
def test_invalid_password_input_is_bounded_before_any_kdf(passwords, monkeypatch, invalid):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("An invalid password reached the KDF")
    monkeypatch.setattr(passwords.hashlib, "scrypt", forbidden)
    assert passwords.verify_password(invalid, None) is False
    with pytest.raises(ValueError):
        passwords.hash_password(invalid)


@pytest.mark.parametrize("stored", [None, "", "unknown", "$2b$12$legacy", 
    "$xtrix-visitor-scrypt$v2$32768$8$3$" + "a" * 32 + "$" + "b" * 64,
    "$xtrix-visitor-scrypt$v1$999999999999$8$3$" + "a" * 32 + "$" + "b" * 64,
    "$xtrix-visitor-scrypt$v1$32768$8$3$" + "A" * 32 + "$" + "b" * 64,
    "$xtrix-visitor-scrypt$v1$32768$8$3$" + "a" * 31 + "$" + "b" * 64,
    "$xtrix-visitor-scrypt$v1$32768$8$3$" + "a" * 32 + "$" + "b" * 64 + "\n",
    "x" * 1000000])
def test_malformed_hash_burns_one_fixed_cost_dummy_and_never_authenticates(passwords, monkeypatch, stored):
    calls = []
    def derive(raw, **kwargs):
        calls.append((raw, kwargs))
        return b"\0" * 32
    def no_hash_generation(*_args, **_kwargs):
        raise AssertionError("Dummy verification must not generate another password hash or salt")
    monkeypatch.setattr(passwords.hashlib, "scrypt", derive)
    monkeypatch.setattr(passwords.secrets, "token_bytes", no_hash_generation)
    assert passwords.verify_password("Candidate!Password-26", stored) is False
    assert len(calls) == 1
    raw, options = calls[0]
    assert raw == b"Candidate!Password-26"
    assert len(options["salt"]) == 16
    assert {key: options[key] for key in ["n", "r", "p", "maxmem", "dklen"]} == {
        "n": 32768, "r": 8, "p": 3, "maxmem": 64 * 1024 * 1024, "dklen": 32}


def test_real_crm_bcrypt_hash_has_no_visitor_fallback(passwords):
    raw = "Synthetic!CRM-Password-26"
    crm = bcrypt.hashpw(raw.encode(), bcrypt.gensalt()).decode("ascii")
    assert bcrypt.checkpw(raw.encode(), crm.encode())
    assert passwords.verify_password(raw, crm) is False


@pytest.mark.parametrize("operation", ["hash", "verify"])
def test_missing_scrypt_is_fail_closed_without_a_fast_fallback(passwords, monkeypatch, operation):
    monkeypatch.setattr(passwords.hashlib, "scrypt", None)
    with pytest.raises(RuntimeError, match="Visitor password hashing is unavailable"):
        if operation == "hash":
            passwords.hash_password("Candidate!Password-26")
        else:
            passwords.verify_password("Candidate!Password-26", None)


def test_runtime_errors_do_not_include_crypto_input_or_internal_error_text(passwords, monkeypatch):
    canary = "private-error-canary-do-not-echo"
    def broken(*_args, **_kwargs):
        raise ValueError(canary)
    monkeypatch.setattr(passwords.hashlib, "scrypt", broken)
    with pytest.raises(RuntimeError) as caught:
        passwords.verify_password("Candidate!Password-26", None)
    assert canary not in str(caught.value)
    assert caught.value.__suppress_context__ is True


def test_at_most_two_hash_or_dummy_kdfs_run_concurrently(passwords, monkeypatch):
    lock = Lock()
    two_active, third_entered, release = Event(), Event(), Event()
    active = 0
    peak = 0
    def blocked(*_args, **_kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            if active == 2:
                two_active.set()
            if active > 2:
                third_entered.set()
        try:
            assert release.wait(timeout=5)
            return b"1" * 32
        finally:
            with lock:
                active -= 1
    monkeypatch.setattr(passwords.hashlib, "scrypt", blocked)
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(passwords.hash_password, "Candidate!Password-26") for _ in range(2)]
        futures += [executor.submit(passwords.verify_password, "Candidate!Password-26", None) for _ in range(2)]
        try:
            assert two_active.wait(timeout=5)
            assert not third_entered.wait(timeout=0.2)
        finally:
            release.set()
        for future in futures:
            future.result(timeout=5)
    assert peak == 2


def test_semaphore_capacity_is_released_after_two_crypto_exceptions(passwords):
    # A leaked slot must fail within the subprocess timeout, not hang the whole suite.
    code = '''
from app.api.core import visitor_passwords as p
calls = 0
def crypto(*args, **kwargs):
    global calls
    calls += 1
    if calls <= 2:
        raise ValueError("synthetic crypto failure")
    return b"1" * 32
p.hashlib.scrypt = crypto
for _ in range(2):
    try:
        p.hash_password("Candidate!Password-26")
    except RuntimeError:
        pass
    else:
        raise AssertionError("Failure became success")
assert p.hash_password("Candidate!Password-26").startswith("$xtrix-visitor-scrypt$v1$")
assert calls == 3
'''
    root = Path(passwords.__file__).resolve().parents[3]
    env = {**os.environ, "PYTHONPATH": str(root), "PYTHONDONTWRITEBYTECODE": "1"}
    outcome = subprocess.run([sys.executable, "-c", code], env=env, text=True,
                             capture_output=True, timeout=8)
    assert outcome.returncode == 0, outcome.stderr
