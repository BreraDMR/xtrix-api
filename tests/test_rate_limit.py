import os
import subprocess
import sys
from pathlib import Path

from app.api.core import config

ROOT = Path(__file__).resolve().parents[1]

# limits are wired at import time, so the "on" case runs in a fresh interpreter
SCRIPT = """
from fastapi.testclient import TestClient
from app.main import app
c = TestClient(app)
body = {"email": "nobody@example.com", "password": "Wr0ng!pass-word"}
print(",".join(str(c.post("/users/login", json=body).status_code) for _ in range(6)))
"""


def test_rate_limit_is_on_by_default():
    assert config.Settings.model_fields["rate_limit_enabled"].default is True


def test_login_is_limited_to_five_per_minute():
    env = {**os.environ, "RATE_LIMIT_ENABLED": "true", "REDIS_URL": "memory://", "PYTHONPATH": str(ROOT)}
    out = subprocess.run([sys.executable, "-c", SCRIPT], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    codes = out.stdout.strip().splitlines()[-1].split(",")
    assert codes == ["401"] * 5 + ["429"], out.stderr[-500:]


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
