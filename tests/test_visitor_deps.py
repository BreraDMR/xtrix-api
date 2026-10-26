"""Private dependency probes: these routes are never registered in the product app."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import secrets
from threading import Event

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import deps, visitor_deps
from app.api.core import error_handlers, subscription_access, visitor_sessions
from app.api.core.i18n import _
from app.api.core.security import create_access_token
from app.db.models.visitor_account import VisitorAccount
from app.db.models.visitor_auth import VisitorSession

NOW = datetime(2026, 10, 25, 12)


def private_app():
    app = FastAPI()
    calls = []
    app.add_exception_handler(StarletteHTTPException, error_handlers.http_exception_handler)
    @app.get("/visitor-read")
    def read(row=Depends(visitor_deps.get_visitor_session)):
        calls.append("read")
        return {"account_id": row.account_id}
    @app.post("/visitor-write")
    def write(row=Depends(visitor_deps.get_locked_visitor_session)):
        calls.append("write")
        return {"account_id": row.account_id}
    @app.get("/crm-only")
    def crm(user=Depends(deps.get_current_user)):
        return {"user_id": user.id}
    return app, calls


@pytest.fixture
def world(db, monkeypatch):
    monkeypatch.setattr(visitor_sessions, "utcnow", lambda: NOW)
    first = VisitorAccount(email_canonical="visitor@example.com", email_verified_at=NOW)
    second = VisitorAccount(email_canonical="other@example.com", email_verified_at=NOW)
    db.add_all([first, second])
    db.flush()
    account = visitor_sessions.lock_verified_account(db, first.id)
    row, raw = visitor_sessions.create_session(db, account)
    result = {"account_id": first.id, "other_id": second.id, "session_id": row.id, "token": raw}
    db.commit()
    return result


@pytest.mark.parametrize("path,method", [("/visitor-read", "get"), ("/visitor-write", "post")])
def test_visitor_header_resolves_only_its_account(db, world, path, method):
    app, calls = private_app()
    with TestClient(app) as client:
        response = getattr(client, method)(path, headers={"X-Visitor-Token": world["token"]})
    assert response.status_code == 200 and response.json() == {"account_id": world["account_id"]}
    assert len(calls) == 1


@pytest.mark.parametrize("locale", ["en", "ru", "cz"])
@pytest.mark.parametrize("headers", [
    {}, {"Authorization": "Bearer some-crm-token"}, {"X-Portal-Token": "legacy-proof"},
    {"X-Booking-Token": "booking-proof"}, {"X-Visitor-Token": "not-a-visitor-session"},
    {"X-Visitor-Token": "vch_" + "A" * 43},
])
def test_missing_or_other_namespace_credentials_have_generic_localized_401(world, locale, headers):
    app, calls = private_app()
    with TestClient(app) as client:
        response = client.get("/visitor-read", headers={"Accept-Language": locale, **headers})
    assert response.status_code == 401
    assert response.json()["error"]["message"] == _("visitor_session_invalid", locale)
    assert "www-authenticate" not in response.headers
    assert calls == []


def test_real_crm_jwt_and_visitor_credentials_are_reciprocally_rejected(world):
    app, _calls = private_app()
    crm = create_access_token({"sub": "1"})
    with TestClient(app) as client:
        assert client.get("/visitor-read", headers={"X-Visitor-Token": crm}).status_code == 401
        assert client.get("/crm-only", headers={"Authorization": "Bearer " + world["token"]}).status_code == 401
        assert client.get("/crm-only", headers={"X-Visitor-Token": world["token"]}).status_code == 401
        assert client.get("/visitor-read", headers={"X-Visitor-Token": secrets.token_urlsafe(32)}).status_code == 401


@pytest.mark.parametrize("path,method", [("/visitor-read", "get"), ("/visitor-write", "post")])
def test_expired_sessions_are_not_renewed_or_accepted(world, monkeypatch, path, method):
    monkeypatch.setattr(visitor_sessions, "utcnow", lambda: NOW + timedelta(days=30))
    app, calls = private_app()
    with TestClient(app) as client:
        response = getattr(client, method)(path, headers={"X-Visitor-Token": world["token"]})
    assert response.status_code == 401 and calls == []


@pytest.mark.parametrize("path,method", [("/visitor-read", "get"), ("/visitor-write", "post")])
def test_revoked_cached_rows_cannot_authenticate_a_second_request(db, world, path, method):
    app, calls = private_app()
    def same_db():
        yield db
    app.dependency_overrides[deps.get_db] = same_db
    cached = db.get(VisitorSession, world["session_id"])
    with TestClient(app) as client:
        assert getattr(client, method)(path, headers={"X-Visitor-Token": world["token"]}).status_code == 200
        # The first locked dependency keeps its account lock until the test's caller finishes.
        db.rollback()
        cached = db.get(VisitorSession, world["session_id"])
        with Session(db.get_bind()) as other:
            visitor_sessions.revoke_all_sessions(other, account_id=world["account_id"])
            other.commit()
        assert cached.revoked_at is None
        response = getattr(client, method)(path, headers={"X-Visitor-Token": world["token"]})
    assert response.status_code == 401 and len(calls) == 1


def test_blocked_crm_with_the_same_email_does_not_block_or_grant_visitor_identity(db, world, make_user, monkeypatch):
    crm = make_user("business_owner", email="visitor@example.com")
    token = create_access_token({"sub": str(crm.id)})
    checked = []
    def blocked(_db, user_id):
        checked.append(user_id)
        return {"reason": "trial_expired", "crm_mode": "blocked", "can_use_ai": False}
    monkeypatch.setattr(subscription_access, "get_subscription_access_state", blocked)
    app, calls = private_app()
    with TestClient(app) as client:
        assert client.get("/crm-only", headers={"Authorization": "Bearer " + token}).status_code == 402
        before = list(checked)
        response = client.get("/visitor-read", headers={"X-Visitor-Token": world["token"], "Authorization": "Bearer " + token})
    assert response.status_code == 200 and response.json() == {"account_id": world["account_id"]}
    assert checked == before and calls == ["read"]


@pytest.mark.parametrize("change", ["revoke", "retarget", "expire"])
def test_locked_http_guard_rechecks_after_waiting_for_account(db, world, monkeypatch, change):
    app, calls = private_app()
    resolved = Event()
    original = visitor_sessions.get_valid_session
    clock = [NOW]
    monkeypatch.setattr(visitor_sessions, "utcnow", lambda: clock[0])
    observed = []
    def observe(session, token):
        row = original(session, token)
        observed.append(row is not None)
        if row is not None:
            resolved.set()
        return row
    monkeypatch.setattr(visitor_sessions, "get_valid_session", observe)
    blocker = Session(db.get_bind())
    try:
        visitor_sessions.lock_verified_account(blocker, world["account_id"])
        with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(client.post, "/visitor-write", headers={"X-Visitor-Token": world["token"]})
            try:
                assert resolved.wait(timeout=5)
                if change == "revoke":
                    visitor_sessions.revoke_all_sessions(blocker, account_id=world["account_id"])
                elif change == "expire":
                    clock[0] = NOW + timedelta(days=30)
                else:
                    # Simulate a stale preflight target. This is not a supported product operation.
                    blocker.execute(update(VisitorSession).where(VisitorSession.id == world["session_id"])
                                    .values(account_id=world["other_id"]))
                blocker.commit()
                response = future.result(timeout=5)
            finally:
                blocker.rollback()
        assert response.status_code == 401 and calls == []
        assert observed == ([True, False] if change in {"revoke", "expire"} else [True, True])
    finally:
        blocker.rollback()
        blocker.close()
