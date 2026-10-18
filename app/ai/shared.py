"""State every worker has to see: resting slots here, kept plans in assistant.py.

Redis when REDIS_URL answers, process memory when it doesn't (a laptop, scripts/check.sh). If Redis
drops out halfway we fall back to memory for that call - a lost plan or one extra 429 from a provider
beats a 500. Keys carry only labels and ids, never keys or texts.
"""
import threading
import time
from datetime import datetime, timedelta
from typing import Callable, Optional

from app.ai.cooldown import MAX_COOLDOWN, CooldownBoard, utc_now
from app.api.core.config import settings
from app.api.core.logger import get_logger

logger = get_logger("ai.shared")
# while Redis is down, look again this often instead of on every request
PROBE_EVERY_SECONDS = 30.0

_lock = threading.Lock()
_client = None
_down_since: Optional[float] = None


def _connect(url: str):
    import redis
    return redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=0.5, socket_timeout=0.5)


def shared_redis():
    """A live Redis client, or None to use process memory."""
    global _client, _down_since
    with _lock:
        if _client is not None:
            return _client
        if _down_since is not None and time.monotonic() - _down_since < PROBE_EVERY_SECONDS:
            return None
        url = settings.redis_url or ""
        if not url.startswith(("redis://", "rediss://")):
            _down_since = time.monotonic()
            return None
        try:
            client = _connect(url)
            client.ping()
        except Exception as exc:
            if _down_since is None:
                logger.warning("ai shared state: redis unreachable (%s), using process memory", type(exc).__name__)
            _down_since = time.monotonic()
            return None
        _client, _down_since = client, None
        return client


def reset_shared_redis() -> None:
    global _client, _down_since
    with _lock:
        _client, _down_since = None, None


class SharedCooldownBoard:
    """Same interface as CooldownBoard. The rest is written to Redis and to local memory, and the
    longer of the two wins - so a Redis hiccup never forgets a rest this worker already knows about."""

    def __init__(self, namespace: str, *, redis: Callable = shared_redis,
                 clock: Optional[Callable[[], datetime]] = None):
        self.namespace = namespace
        self.redis = redis
        self.clock = clock or utc_now
        self.memory = CooldownBoard(clock=self.clock)

    def _key(self, key: str) -> str:
        return f"ai:cool:{self.namespace}:{key}"

    def _remote_until(self, client, key: str) -> Optional[float]:
        raw = client.get(self._key(key))
        try:
            return float(raw) if raw is not None else None
        except ValueError:
            return None

    def cool(self, key: str, *, seconds: Optional[float] = None, until: Optional[datetime] = None) -> float:
        now = self.clock()
        if until is None:
            until = now + timedelta(seconds=max(1.0, seconds or 0))
        until = min(until, now + MAX_COOLDOWN)
        self.memory.cool(key, until=until)
        client = self.redis()
        if client is not None:
            try:
                current = self._remote_until(client, key)
                # a longer rest already set wins; two workers racing here can at worst keep the shorter one
                if current is None or current < until.timestamp():
                    ms = max(1, int((until - now).total_seconds() * 1000))
                    client.set(self._key(key), f"{until.timestamp():.3f}", px=ms)
            except Exception as exc:
                logger.warning("ai shared state: cooldown write failed (%s)", type(exc).__name__)
        return self.remaining(key)

    def remaining(self, key: str) -> float:
        local = self.memory.remaining(key)
        client = self.redis()
        if client is None:
            return local
        try:
            until = self._remote_until(client, key)
        except Exception as exc:
            logger.warning("ai shared state: cooldown read failed (%s)", type(exc).__name__)
            return local
        remote = max(0.0, until - self.clock().timestamp()) if until is not None else 0.0
        return max(local, remote)

    def clear(self) -> None:
        self.memory.clear()
        client = self.redis()
        if client is None:
            return
        try:
            keys = list(client.scan_iter(match=self._key("*")))
            if keys:
                client.delete(*keys)
        except Exception as exc:
            logger.warning("ai shared state: cooldown clear failed (%s)", type(exc).__name__)
