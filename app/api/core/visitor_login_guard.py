"""Visitor login failures share the CRM policy, with independent opaque keys.

Call check before any account lookup, failed after every generic credential
failure (including unknown email), and succeeded only after session commit.
No visitor/account id or database lookup belongs in this guard.
"""
import hashlib
import time

from limits import parse
from limits.storage import storage_from_string
from limits.strategies import MovingWindowRateLimiter

from app.api.core.config import settings
from app.db.models.visitor_account import canonical_visitor_email
from app.api.middleware.rate_limit import _STORAGE_URI

_strategy = MovingWindowRateLimiter(storage_from_string(_STORAGE_URI))
_NS = "visitor-login-fail"


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


def _identity(email: str) -> str:
    return hashlib.sha256(canonical_visitor_email(email).encode("utf-8")).hexdigest()


def check(email: str, ip: str) -> None:
    """Refuse this email/IP pair; a clean IP still follows the existing policy."""
    pair, account = _pair_limit(), _account_limit()
    uid = _identity(email)
    if not _strategy.test(pair, _NS, uid, ip):
        raise LoginThrottled(_wait(pair, uid, ip))
    if not _strategy.test(account, _NS, uid):
        pair_failures = pair.amount - _strategy.get_window_stats(pair, _NS, uid, ip).remaining
        if pair_failures > 0:
            raise LoginThrottled(_wait(account, uid))


def failed(email: str, ip: str) -> None:
    uid = _identity(email)
    _strategy.hit(_pair_limit(), _NS, uid, ip)
    _strategy.hit(_account_limit(), _NS, uid)


def succeeded(email: str, ip: str) -> None:
    _strategy.clear(_pair_limit(), _NS, _identity(email), ip)
