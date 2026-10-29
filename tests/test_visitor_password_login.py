"""Visitor password login never creates a CRM identity or a salon subscription."""
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import json
import os
import subprocess
import sys

import pytest
from sqlalchemy.orm import Session

from app.api.core import visitor_sessions
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.subscription import Subscription
from app.db.models.user import User
from app.db.models.visitor_account import VisitorAccount
from app.db.models.visitor_auth import VisitorSession

URL = "/visitor/auth/login"
PASSWORD = "Long!Visitor-password-26"


@pytest.fixture(autouse=True)
def isolated_failure_counters(monkeypatch):
    from limits.storage import MemoryStorage
    from limits.strategies import MovingWindowRateLimiter
    from app.api.core import visitor_login_guard

    storage = MemoryStorage()
    monkeypatch.setattr(visitor_login_guard, "_strategy", MovingWindowRateLimiter(storage))
    yield
    if storage.timer:
        storage.timer.cancel()
    storage.reset()


def private(response, status):
    assert response.status_code == status, response.text
    assert response.headers.get("cache-control") == "no-store"
    assert "set-cookie" not in response.headers


def test_unknown_email_is_generic_private_401_without_creating_any_identity(client, db):
    tables = (VisitorAccount, VisitorSession, User, Subscription, NotificationOutbox)
    before = [db.query(table).count() for table in tables]
    response = client.post(URL, json={"email": "unknown@example.com", "password": PASSWORD})
    private(response, 401)
    assert PASSWORD not in response.text and "unknown@example.com" not in response.text
    assert [db.query(table).count() for table in tables] == before


@pytest.mark.parametrize("body", [
    {"email": "x@example.com"},
    {"email": "x@example.com", "password": PASSWORD, "account_id": 1},
    {"email": "x@example.com", "password": True},
    {"email": "x@example.com", "password": "x" * 129},
])
def test_invalid_login_body_is_private_and_does_not_echo_secrets(client, body):
    response = client.post(URL, json=body)
    private(response, 422)
    assert PASSWORD not in response.text


@pytest.fixture
def visitor(db):
    from app.api.core.visitor_passwords import hash_password

    row = VisitorAccount(email_canonical="visitor.login@example.com",
        email_verified_at=datetime(2026, 1, 1), hashed_password=hash_password(PASSWORD))
    db.add(row)
    db.commit()
    return row.id


def test_password_login_issues_only_opaque_fixed_expiry_session(client, db, visitor):
    before = [db.query(table).count() for table in (User, Subscription, NotificationOutbox)]
    response = client.post(URL, json={"email": "  VISITOR.LOGIN@EXAMPLE.COM  ", "password": PASSWORD})
    private(response, 200)
    data = response.json()
    assert set(data) == {"session_token", "expires_at"}
    assert data["session_token"].startswith("vst_") and data["expires_at"].endswith("Z")
    session = visitor_sessions.get_valid_session(db, data["session_token"])
    assert session.account_id == visitor
    assert session.expires_at - session.created_at == timedelta(days=30)
    assert session.token_hash not in response.text
    profile = client.get("/visitor/me", headers={"X-Visitor-Token": data["session_token"]})
    private(profile, 200)
    assert profile.json()["id"] == visitor
    assert [db.query(table).count() for table in (User, Subscription, NotificationOutbox)] == before


@pytest.mark.parametrize("kind", ["wrong", "google_only", "corrupt", "crm_hash"])
def test_wrong_or_unsupported_password_never_issues_a_session(client, db, visitor, kind):
    row = db.get(VisitorAccount, visitor)
    if kind == "google_only":
        row.hashed_password = None
    elif kind == "corrupt":
        row.hashed_password = "unsupported-password-format"
    elif kind == "crm_hash":
        from app.api.core.security import get_password_hash
        row.hashed_password = get_password_hash(PASSWORD)
    db.commit()
    response = client.post(URL, json={"email": row.email_canonical,
        "password": "Wrong!Password-26" if kind == "wrong" else PASSWORD})
    private(response, 401)
    assert db.query(VisitorSession).count() == 0
    assert PASSWORD not in response.text


def test_failure_limit_stops_only_the_attempting_ip_and_success_resets_its_pair(client, db, visitor, monkeypatch):
    from app.api.core.config import settings

    monkeypatch.setattr(settings, "trust_proxy", True)
    def attempt(ip, password):
        return client.post(URL, json={"email": "visitor.login@example.com", "password": password},
                           headers={"X-Forwarded-For": ip})
    for _ in range(settings.max_login_attempts):
        private(attempt("203.0.113.50", "Wrong!Password-26"), 401)
    blocked = attempt("203.0.113.50", PASSWORD)
    private(blocked, 429)
    assert int(blocked.headers["retry-after"]) > 0
    private(attempt("198.51.100.10", PASSWORD), 200)
    for _ in range(settings.max_login_attempts - 1):
        private(attempt("198.51.100.10", "Wrong!Password-26"), 401)
    private(attempt("198.51.100.10", PASSWORD), 200)
    private(attempt("198.51.100.10", "Wrong!Password-26"), 401)


@pytest.mark.parametrize("change", ["password", "email", "delete"])
def test_login_checks_fresh_credentials_after_waiting_for_account_lock(client, db, visitor, monkeypatch, change):
    from app.api.core import visitor_passwords

    original = visitor_sessions.lock_verified_account
    entering_lock = Event()
    def observe(session, account_id):
        entering_lock.set()
        return original(session, account_id)
    with Session(db.get_bind()) as blocker:
        account = original(blocker, visitor)
        monkeypatch.setattr(visitor_sessions, "lock_verified_account", observe)
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(client.post, URL,
                json={"email": "visitor.login@example.com", "password": PASSWORD})
            try:
                assert entering_lock.wait(5)
                if change == "password":
                    account.hashed_password = visitor_passwords.hash_password("Changed!Password-26")
                    visitor_sessions.revoke_all_sessions(blocker, account_id=visitor)
                elif change == "email":
                    account.email_canonical = "changed@example.com"
                else:
                    blocker.delete(account)
                blocker.commit()
                response = pending.result(10)
            finally:
                blocker.rollback()
    private(response, 401)
    assert db.query(VisitorSession).count() == 0


def test_failed_session_commit_returns_no_token_and_rolls_back(client, db, visitor, monkeypatch):
    from fastapi.testclient import TestClient
    from app.api.deps import get_db
    from app.main import app

    def broken_db():
        with Session(db.get_bind()) as transaction:
            def fail():
                raise RuntimeError("synthetic visitor commit failure")
            monkeypatch.setattr(transaction, "commit", fail)
            yield transaction
    app.dependency_overrides[get_db] = broken_db
    try:
        with TestClient(app, raise_server_exceptions=False) as failing_client:
            response = failing_client.post(URL,
                json={"email": "visitor.login@example.com", "password": PASSWORD})
        assert response.status_code == 500
        assert "session_token" not in response.text and PASSWORD not in response.text
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert db.query(VisitorSession).count() == 0


@pytest.mark.parametrize("layer", ["route", "default"])
def test_actual_traffic_limits_return_private_429_with_retry_after(layer):
    code = '''
import json
from fastapi.testclient import TestClient
from app.main import app
with TestClient(app) as client:
    responses = [client.post('/visitor/auth/login', json={
        'email':'traffic-limit@example.com', 'password':'Synthetic!Password-26'}) for _ in range(3)]
print(json.dumps([{'status':r.status_code, 'cache':r.headers.get('cache-control'),
    'retry':r.headers.get('retry-after')} for r in responses]))
'''
    env = {**os.environ, "RATE_LIMIT_ENABLED": "true", "REDIS_URL": "memory://",
           "RATE_LIMIT_LOGIN": "2/minute" if layer == "route" else "100/minute",
           "RATE_LIMIT_DEFAULT": "2/minute" if layer == "default" else "100/minute"}
    outcome = subprocess.run([sys.executable, "-c", code], env=env,
        capture_output=True, text=True, timeout=40)
    assert outcome.returncode == 0, outcome.stderr[-1500:]
    results = json.loads(outcome.stdout.strip().splitlines()[-1])
    assert [r["status"] for r in results] == [401, 401, 429]
    assert all(r["cache"] == "no-store" for r in results)
    assert int(results[-1]["retry"]) > 0
