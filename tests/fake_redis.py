"""A tiny in-memory stand-in for the few Redis commands the assistant uses.

fakeredis isn't a dependency, and this is all we need: SET with EX/PX/NX, GET, GETDEL, DELETE,
SCAN_ITER, PING - with expiry driven by a clock the test controls.
"""
from datetime import datetime, timezone

from redis.exceptions import ConnectionError as RedisConnectionError


class FakeRedis:
    def __init__(self, clock=None):
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.data: dict[str, tuple[str, float | None]] = {}
        self.down = False  # flip to make every call fail like a dropped connection
        self.calls: list[str] = []

    def _now(self) -> float:
        return self.clock().timestamp()

    def _check(self, name):
        self.calls.append(name)
        if self.down:
            raise RedisConnectionError("fake redis is down")

    def _alive(self, key):
        item = self.data.get(key)
        if item is None:
            return None
        value, expires = item
        if expires is not None and expires <= self._now():
            del self.data[key]
            return None
        return value

    def ping(self):
        self._check("ping")
        return True

    def set(self, key, value, ex=None, px=None, nx=False):
        self._check("set")
        if nx and self._alive(key) is not None:
            return None
        ttl = ex if ex is not None else (px / 1000 if px is not None else None)
        self.data[key] = (str(value), self._now() + ttl if ttl is not None else None)
        return True

    def get(self, key):
        self._check("get")
        return self._alive(key)

    def getdel(self, key):
        self._check("getdel")
        value = self._alive(key)
        self.data.pop(key, None)
        return value

    def delete(self, *keys):
        self._check("delete")
        return sum(self.data.pop(k, None) is not None for k in keys)

    def scan_iter(self, match=None):
        self._check("scan")
        prefix = (match or "*").rstrip("*")
        return [k for k in list(self.data) if k.startswith(prefix) and self._alive(k) is not None]
