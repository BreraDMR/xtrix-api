"""Brute-force protection for login that can't be turned against the account owner.

It used to lock the account itself after 5 wrong passwords from anywhere - so anyone who knew
your e-mail could keep you out for 30 minutes. Now failures are counted per (account, IP):
that pair is refused after max_login_attempts within lockout_duration_minutes, other addresses
aren't affected. On top, a cap on failures per account from all IPs together; past it only
addresses with no failures on this account may try (the owner from a clean IP still gets in).

Counters live in the rate-limit store (Redis when there is one), always on - this isn't a
traffic limit, so RATE_LIMIT_ENABLED doesn't switch it off.
"""
import time

from limits import parse
from limits.storage import storage_from_string
from limits.strategies import MovingWindowRateLimiter

from app.api.core.config import settings
from app.api.middleware.rate_limit import _STORAGE_URI

_strategy = MovingWindowRateLimiter(storage_from_string(_STORAGE_URI))
_NS = "login-fail"


class LoginThrottled(Exception):
    def __init__(self, retry_after: int):
        super().__init__(f"login throttled for {retry_after} s")
        self.retry_after = retry_after


def _pair_limit():
    return parse(f"{settings.max_login_attempts} per {settings.lockout_duration_minutes} minutes")


def _account_limit():
    return parse(settings.login_account_failure_limit)


def _wait(item, *ids) -> int:
    reset = _strategy.get_window_stats(item, _NS, *ids).reset_time
    return max(1, int(reset - time.time()))


def check(user_id: int, ip: str) -> None:
    """Raise LoginThrottled if this address may not try this account right now."""
    pair, account = _pair_limit(), _account_limit()
    uid = str(user_id)
    if not _strategy.test(pair, _NS, uid, ip):
        raise LoginThrottled(_wait(pair, uid, ip))
    if not _strategy.test(account, _NS, uid):
        pair_failures = pair.amount - _strategy.get_window_stats(pair, _NS, uid, ip).remaining
        if pair_failures > 0:
            raise LoginThrottled(_wait(account, uid))


def failed(user_id: int, ip: str) -> None:
    uid = str(user_id)
    _strategy.hit(_pair_limit(), _NS, uid, ip)
    _strategy.hit(_account_limit(), _NS, uid)


def succeeded(user_id: int, ip: str) -> None:
    _strategy.clear(_pair_limit(), _NS, str(user_id), ip)
