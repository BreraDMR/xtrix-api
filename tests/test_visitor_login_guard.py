"""Visitor and CRM guards share a real store but never failure counters."""
import hashlib
import time

import pytest
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter

from app.api.core import login_guard as crm
from app.api.core import visitor_login_guard as visitor
from app.api.core.config import settings


@pytest.fixture
def guards(monkeypatch):
    monkeypatch.setattr(settings, "max_login_attempts", 5)
    monkeypatch.setattr(settings, "lockout_duration_minutes", 30)
    monkeypatch.setattr(settings, "login_account_failure_limit", "50/hour")
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    storage = MemoryStorage()
    strategy = MovingWindowRateLimiter(storage)
    monkeypatch.setattr(visitor, "_strategy", strategy)
    monkeypatch.setattr(crm, "_strategy", strategy)
    yield visitor, crm, settings, storage
    if storage.timer:
        storage.timer.cancel()
    storage.reset()


def fail(g, email, ip, count):
    for _ in range(count):
        g.check(email, ip)
        g.failed(email, ip)


def test_pair_cap_leaves_owner_clean_ip_and_other_email_free(guards):
    g, _, _, _ = guards
    fail(g, 'owner@example.com', '203.0.113.1', 5)
    with pytest.raises(g.LoginThrottled):
        g.check('owner@example.com', '203.0.113.1')
    g.check('owner@example.com', '198.51.100.1')
    g.check('other@example.com', '203.0.113.1')


def test_account_cap_only_refuses_dirty_ips(guards):
    g, _, settings, _ = guards
    settings.login_account_failure_limit = '3/hour'
    for n in range(3):
        fail(g, 'owner@example.com', f'203.0.113.{n}', 1)
    with pytest.raises(g.LoginThrottled):
        g.check('owner@example.com', '203.0.113.0')
    g.check('owner@example.com', '198.51.100.1')


def test_succeeded_clears_only_that_pair_not_other_ip_email_or_account(guards):
    g, _, settings, _ = guards
    settings.login_account_failure_limit = '3/hour'
    fail(g, 'owner@example.com', '203.0.113.1', 2)
    fail(g, 'owner@example.com', '203.0.113.2', 1)
    fail(g, 'other@example.com', '203.0.113.1', 3)
    g.succeeded('owner@example.com', '203.0.113.1')
    g.check('owner@example.com', '203.0.113.1')
    with pytest.raises(g.LoginThrottled):
        g.check('owner@example.com', '203.0.113.2')
    with pytest.raises(g.LoginThrottled):
        g.check('other@example.com', '203.0.113.1')
    # The aggregate cap survives success: one more typo makes the clean pair dirty again.
    g.failed('owner@example.com', '203.0.113.1')
    with pytest.raises(g.LoginThrottled):
        g.check('owner@example.com', '203.0.113.1')


def test_crm_and_visitor_share_no_failure_keys_or_resets(guards):
    g, crm, _, store = guards
    fail(g, 'owner@example.com', '203.0.113.1', 5)
    crm.check(1, '203.0.113.1')
    for _ in range(5):
        crm.failed(1, '203.0.113.1')
    g.succeeded('owner@example.com', '203.0.113.1')
    with pytest.raises(crm.LoginThrottled):
        crm.check(1, '203.0.113.1')
    crm.succeeded(1, '203.0.113.1')
    fail(g, 'owner@example.com', '203.0.113.1', 5)
    crm.succeeded(1, '203.0.113.1')
    with pytest.raises(g.LoginThrottled):
        g.check('owner@example.com', '203.0.113.1')
    keys = list(store.events)
    assert any('/visitor-login-fail/' in key for key in keys)
    assert any('/login-fail/' in key for key in keys)
    assert not any('owner@example.com' in key for key in keys)


@pytest.mark.parametrize('email', ['known@example.com', 'unknown@example.com'])
def test_known_unknown_use_the_same_failure_path_without_database(guards, email):
    g, _, _, _ = guards
    fail(g, email, '203.0.113.1', 5)
    with pytest.raises(g.LoginThrottled):
        g.check(email, '203.0.113.1')


def test_shared_canonicalizer_and_sha256_keys_without_gmail_merging(guards):
    g, _, _, store = guards
    fail(g, '  Visitor+Tag@EXAMPLE.COM  ', '203.0.113.1', 5)
    with pytest.raises(g.LoginThrottled):
        g.check('visitor+tag@example.com', '203.0.113.1')
    digest = hashlib.sha256(b'visitor+tag@example.com').hexdigest()
    assert all(digest in key for key in store.events)
    g.check('visitor@example.com', '203.0.113.1')
    assert g._identity('STRAßE@example.com') == g._identity('strasse@example.com')
    assert g._identity('a.b@gmail.com') != g._identity('ab@gmail.com')


def test_guard_still_runs_when_traffic_limiter_is_disabled(guards):
    g, _, settings, _ = guards
    assert settings.rate_limit_enabled is False
    fail(g, 'unknown@example.com', '203.0.113.1', 5)
    with pytest.raises(g.LoginThrottled):
        g.check('unknown@example.com', '203.0.113.1')


def test_pair_ttl_and_retry_after_use_existing_moving_window_clock(guards, monkeypatch):
    g, _, _, _ = guards
    now = [2_000_000_000.0]
    monkeypatch.setattr(time, 'time', lambda: now[0])
    fail(g, 'unknown@example.com', '203.0.113.1', 5)
    with pytest.raises(g.LoginThrottled) as exc:
        g.check('unknown@example.com', '203.0.113.1')
    assert exc.value.retry_after == 1800
    now[0] += 1799
    with pytest.raises(g.LoginThrottled) as exc:
        g.check('unknown@example.com', '203.0.113.1')
    assert exc.value.retry_after == 1
    now[0] += 2
    g.check('unknown@example.com', '203.0.113.1')


def test_account_retry_after_and_ttl_are_independent_of_pair_expiry(guards, monkeypatch):
    g, _, settings, _ = guards
    settings.login_account_failure_limit = '2/hour'
    now = [2_000_000_000.0]
    monkeypatch.setattr(time, 'time', lambda: now[0])
    fail(g, 'owner@example.com', '203.0.113.1', 1)
    fail(g, 'owner@example.com', '203.0.113.2', 1)
    with pytest.raises(g.LoginThrottled) as exc:
        g.check('owner@example.com', '203.0.113.1')
    assert exc.value.retry_after == 3600
    now[0] += 1801
    # Same as CRM: expired pair is clean again, even while aggregate cap remains reached.
    g.check('owner@example.com', '203.0.113.1')
    g.failed('owner@example.com', '203.0.113.1')
    with pytest.raises(g.LoginThrottled) as exc:
        g.check('owner@example.com', '203.0.113.1')
    assert exc.value.retry_after == 1799
    now[0] += 1800
    g.check('owner@example.com', '203.0.113.1')
