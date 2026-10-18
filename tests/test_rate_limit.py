import os
import subprocess
import sys
from pathlib import Path

from app.api.core import config

ROOT = Path(__file__).resolve().parents[1]

# limits are wired at import time, so the "on" cases run in a fresh interpreter
LOGIN = """
from fastapi.testclient import TestClient
from app.main import app
c = TestClient(app)
body = {"email": "nobody@example.com", "password": "Wr0ng!pass-word"}
rs = [c.post("/users/login", json=body) for _ in range(11)]
print(",".join(str(r.status_code) for r in rs) + "|" + str(rs[-1].json()["error"]["status_code"]) + "|" + rs[-1].headers.get("retry-after", ""))
"""

DEFAULT = """
import json
from fastapi.testclient import TestClient
from app.main import app
c = TestClient(app)
probes = [c.get("/metrics/health/live").status_code for _ in range(5)]
plans = [c.get("/subscriptions/plans") for _ in range(4)]
last = plans[-1]
print(json.dumps({"probes": probes, "plans": [r.status_code for r in plans],
                  "body": last.json(), "retry_after": last.headers.get("retry-after")}))
"""

SIGNUP = """
from fastapi.testclient import TestClient
from app.main import app
c = TestClient(app)
body = {"full_name": "x", "role": "admin", "password": "Str0ng!Pass-word"}
print(",".join(str(c.post("/users/", json=body).status_code) for _ in range(6)))
"""


def _run(script, **env_extra):
    env = {**os.environ, "RATE_LIMIT_ENABLED": "true", "REDIS_URL": "memory://", "PYTHONPATH": str(ROOT), **env_extra}
    out = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-1500:]
    return out.stdout.strip().splitlines()[-1]


def test_rate_limit_is_on_by_default():
    assert config.Settings.model_fields["rate_limit_enabled"].default is True


def test_login_is_limited_per_ip():
    # 10 a minute per IP: a salon's staff often sit behind one address
    codes, error_status, retry_after = _run(LOGIN).split("|")
    assert codes.split(",") == ["401"] * 10 + ["429"]
    assert error_status == "429" and int(retry_after) > 0


def test_default_limit_applies_to_every_route_but_health_probes():
    import json

    res = json.loads(_run(DEFAULT, RATE_LIMIT_DEFAULT="3/minute"))
    assert res["probes"] == [200] * 5
    assert res["plans"] == [200, 200, 200, 429]
    # same error shape as everything else, and the client is told when to come back
    assert res["body"]["error"]["status_code"] == 429
    assert res["body"]["error"]["message"]
    assert int(res["retry_after"]) > 0


def test_signup_is_rate_limited():
    # even rejected attempts count - a 403 per try is no reason to allow unlimited probing
    assert _run(SIGNUP).split(",") == ["403"] * 5 + ["429"]


def test_client_ip_ignores_spoofed_forwarded_for_unless_behind_our_proxy(monkeypatch):
    from starlette.requests import Request

    from app.api.middleware.rate_limit import client_ip

    def req(xff):
        headers = [(b"x-forwarded-for", xff.encode())] if xff else []
        return Request({"type": "http", "headers": headers, "client": ("10.0.0.5", 1234)})

    monkeypatch.setattr(config.settings, "trust_proxy", False)
    assert client_ip(req("1.2.3.4")) == "10.0.0.5"  # straight to us: header is just client input
    monkeypatch.setattr(config.settings, "trust_proxy", True)
    # behind Render: the proxy appends the real peer last; the client's own prefix doesn't matter
    assert client_ip(req("6.6.6.6, 203.0.113.7")) == "203.0.113.7"
    assert client_ip(req("")) == "10.0.0.5"


def test_browser_on_another_origin_can_read_retry_after(client):
    # the web app talks to the API cross-origin in production; without this header
    # listed the browser hides Retry-After from JS
    r = client.get("/", headers={"Origin": "http://localhost:5173"})
    exposed = [h.strip().lower() for h in r.headers.get("access-control-expose-headers", "").split(",")]
    assert "retry-after" in exposed
