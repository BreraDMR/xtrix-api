import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# every way the API says 429 should carry the same security headers as a normal 200:
# the default per-IP limit, a per-route slowapi limit and the login guard's account_locked
SCRIPT = """
import json
from fastapi.testclient import TestClient
from app.main import app
c = TestClient(app)
ok = c.get("/subscriptions/plans")
default = [c.get("/subscriptions/plans") for _ in range(5)][-1]
body = {"email": "nobody@example.com", "password": "Wr0ng!pass-word"}
login = [c.post("/users/login", json=body, headers={"X-Forwarded-For": "198.51.100.9"}) for _ in range(12)][-1]
def pick(r):
    return {"status": r.status_code, "headers": {k.lower(): v for k, v in r.headers.items()}}
print(json.dumps({"ok": pick(ok), "default": pick(default), "login": pick(login)}))
"""

SECURITY = (
    "x-content-type-options", "x-frame-options", "x-xss-protection", "referrer-policy",
    "permissions-policy", "content-security-policy", "x-permitted-cross-domain-policies",
)


def _run(**env_extra):
    env = {**os.environ, "RATE_LIMIT_ENABLED": "true", "REDIS_URL": "memory://",
           "PYTHONPATH": str(ROOT), "RATE_LIMIT_DEFAULT": "100/minute", **env_extra}
    out = subprocess.run([sys.executable, "-c", SCRIPT], cwd=ROOT, env=env, capture_output=True, text=True, timeout=90)
    assert out.returncode == 0, out.stderr[-1500:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def _assert_same_security_headers(ok, limited):
    assert ok["status"] == 200
    assert limited["status"] == 429
    for name in SECURITY:
        assert limited["headers"].get(name) == ok["headers"].get(name), name
    assert "server" not in limited["headers"]


def test_per_route_limit_and_login_guard_429_carry_security_headers():
    res = _run()
    _assert_same_security_headers(res["ok"], res["login"])


def test_default_limit_429_carries_security_headers():
    res = _run(RATE_LIMIT_DEFAULT="3/minute")
    _assert_same_security_headers(res["ok"], res["default"])
    assert res["default"]["headers"]["x-content-type-options"] == "nosniff"
