"""Which slots are resting after a rate limit, and until when.

This board is per process; shared.SharedCooldownBoard keeps the same thing in Redis so every worker
sees a rest at once, and falls back to this one when Redis isn't there.
"""
import threading
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

# never park a slot longer than this, whatever a provider says
MAX_COOLDOWN = timedelta(hours=26)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def next_utc_midnight(now: datetime) -> datetime:
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


class CooldownBoard:
    def __init__(self, clock: Optional[Callable[[], datetime]] = None):
        self.clock = clock or utc_now
        self._until: dict[str, datetime] = {}
        self._lock = threading.Lock()

    def cool(self, key: str, *, seconds: Optional[float] = None, until: Optional[datetime] = None) -> float:
        now = self.clock()
        if until is None:
            until = now + timedelta(seconds=max(1.0, seconds or 0))
        until = min(until, now + MAX_COOLDOWN)
        with self._lock:
            # a longer rest already set wins over a shorter new one
            if key not in self._until or self._until[key] < until:
                self._until[key] = until
        return self.remaining(key)

    def remaining(self, key: str) -> float:
        with self._lock:
            until = self._until.get(key)
            if until is None:
                return 0.0
            left = (until - self.clock()).total_seconds()
            if left <= 0:
                del self._until[key]
                return 0.0
            return left

    def clear(self) -> None:
        with self._lock:
            self._until.clear()
