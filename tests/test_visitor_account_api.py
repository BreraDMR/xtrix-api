"""Actual registered visitor routes, with opaque credentials and real PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Event

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.api.core import subscription_access, visitor_challenges, visitor_sessions
from app.api.core.i18n import _
from app.api.core.security import create_access_token, get_password_hash
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.subscription import Subscription
from app.db.models.user import User
from app.db.models.visitor_account import VisitorAccount, VisitorGoogleIdentity
from app.db.models.visitor_auth import VisitorChallenge, VisitorSession

NOW = datetime(2026, 10, 25, 15, 30, 0, 123456)
LOGOUTS = ["/visitor/auth/logout", "/visitor/auth/logout-all"]
PROFILE_FIELDS = {"id", "email", "email_verified_at", "created_at", "has_password", "google_connected"}


@pytest.fixture
def world(db, monkeypatch):
    clock = [NOW]
    monkeypatch.setattr(visitor_sessions, "utcnow", lambda: clock[0])
    before = [db.query(table).count() for table in (User, Subscription, NotificationOutbox)]
    first = VisitorAccount(email_canonical="visitor.first@example.com", email_verified_at=NOW,
        created_at=NOW, hashed_password=get_password_hash("First!Visitor-pass-26"))
    second = VisitorAccount(email_canonical="visitor.second@example.com", email_verified_at=NOW,
        created_at=NOW, hashed_password=None)
    db.add_all([first, second])
    db.flush()
    db.add(VisitorGoogleIdentity(account_id=second.id, issuer="https://accounts.google.com",
        subject="private-google-subject-canary"))
    rows = []
    for account in [first, first, second]:
        row, raw = visitor_sessions.create_session(db, account)
        rows.append({"id": row.id, "raw": raw, "account_id": account.id})
    result = {"first": first.id, "second": second.id, "sessions": rows,
              "clock": clock, "before": before}
    db.commit()
    return result


def no_store(response, status):
    assert response.status_code == status, response.text
    assert response.headers.get("cache-control") == "no-store"
    if status == 204:
        assert response.content == b""


def session_rows(db):
    return [dict(row) for row in db.execute(select(VisitorSession.__table__)
        .order_by(VisitorSession.id)).mappings()]


def test_registered_profile_has_only_its_own_allowlist_and_utc_z_fields(client, db, world):
    for index, owner_id, password, google in [(0, world["first"], True, False), (2, world["second"], False, True)]:
        raw = world["sessions"][index]["raw"]
        response = client.get("/visitor/me", headers={"X-Visitor-Token": raw})
        no_store(response, 200)
        data = response.json()
        assert set(data) == PROFILE_FIELDS
        assert data == {"id": owner_id, "email": "visitor.first@example.com" if index == 0 else "visitor.second@example.com",
            "email_verified_at": NOW.isoformat() + "Z", "created_at": NOW.isoformat() + "Z",
            "has_password": password, "google_connected": google}
        assert raw not in response.text and "private-google-subject-canary" not in response.text
        assert "hashed_password" not in response.text and "business_id" not in response.text
    assert [db.query(table).count() for table in (User, Subscription, NotificationOutbox)] == world["before"]


def test_profile_and_logout_never_enter_crm_subscription_or_mail_gates(client, world, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Visitor endpoint entered the CRM subscription gate")
    monkeypatch.setattr(subscription_access, "enforce_business_access", forbidden)
    monkeypatch.setattr(subscription_access, "enforce_subscription_request_access", forbidden)
    headers = {"X-Visitor-Token": world["sessions"][0]["raw"], "Authorization": "Bearer unrelated-crm-token"}
    no_store(client.get("/visitor/me", headers=headers), 200)
    no_store(client.post(LOGOUTS[0], headers=headers), 204)


@pytest.mark.parametrize("locale", ["en", "ru", "cz"])
def test_profile_missing_credential_has_generic_localized_no_store_401(client, world, locale):
    response = client.get("/visitor/me", headers={"Accept-Language": locale})
    no_store(response, 401)
    assert response.json()["error"]["message"] == _("visitor_session_invalid", locale)
    assert "www-authenticate" not in response.headers


@pytest.mark.parametrize("source", ["authorization", "cookie", "body", "query", "legacy"])
def test_valid_token_in_another_source_neither_reads_nor_revokes(client, db, world, source):
    raw = world["sessions"][0]["raw"]
    headers, payload, suffix = {}, {}, ""
    if source == "authorization":
        headers = {"Authorization": "Bearer " + raw}
    elif source == "cookie":
        headers = {"Cookie": "X-Visitor-Token=" + raw}
    elif source == "body":
        payload = {"token": raw, "x_visitor_token": raw}
    elif source == "query":
        suffix = "?token=" + raw
    else:
        headers = {"X-Portal-Token": raw}
    before = session_rows(db)
    no_store(client.request("GET", "/visitor/me" + suffix, headers=headers, json=payload), 401)
    for path in LOGOUTS:
        no_store(client.post(path + suffix, headers=headers, json=payload), 204)
    assert session_rows(db) == before


def test_real_crm_access_token_cannot_be_used_as_visitor_header(client, db, world, make_user):
    crm = make_user("business_owner", email="visitor.first@example.com")
    raw = create_access_token({"sub": str(crm.id), "role": crm.role})
    before = session_rows(db)
    response = client.get("/visitor/me", headers={"X-Visitor-Token": raw})
    no_store(response, 401)
    assert raw not in response.text
    for path in LOGOUTS:
        no_store(client.post(path, headers={"X-Visitor-Token": raw}), 204)
    assert session_rows(db) == before


@pytest.mark.parametrize("path", LOGOUTS)
@pytest.mark.parametrize("kind", ["missing", "unknown", "expired", "revoked"])
def test_invalid_logout_is_idempotent_empty_and_does_not_mutate_any_session(client, db, world, path, kind):
    raw = world["sessions"][0]["raw"]
    if kind == "unknown":
        raw = "vst_" + "A" * 43
    elif kind == "expired":
        db.execute(update(VisitorSession).where(VisitorSession.id == world["sessions"][0]["id"])
            .values(expires_at=NOW + timedelta(seconds=1)))
        db.commit()
        world["clock"][0] = NOW + timedelta(seconds=1)
    elif kind == "revoked":
        visitor_sessions.revoke_session(db, account_id=world["first"], session_id=world["sessions"][0]["id"])
        db.commit()
    headers = {} if kind == "missing" else {"X-Visitor-Token": raw}
    before = session_rows(db)
    no_store(client.post(path, headers=headers), 204)
    no_store(client.post(path, headers=headers), 204)
    assert session_rows(db) == before


@pytest.mark.parametrize("path", LOGOUTS)
def test_header_owns_logout_even_when_body_and_query_target_another_account(client, db, world, path):
    first, sibling, foreign = world["sessions"]
    response = client.post(path + f'?account_id={world["second"]}&session_id={foreign["id"]}',
        headers={"X-Visitor-Token": first["raw"]},
        json={"account_id": world["second"], "session_id": foreign["id"], "token": foreign["raw"]})
    no_store(response, 204)
    db.expire_all()
    assert db.get(VisitorSession, first["id"]).revoked_at == NOW
    assert db.get(VisitorSession, foreign["id"]).revoked_at is None
    assert db.get(VisitorSession, sibling["id"]).revoked_at == (NOW if path.endswith("logout-all") else None)
    no_store(client.post(path, headers={"X-Visitor-Token": first["raw"]}), 204)


@pytest.mark.parametrize("path", LOGOUTS)
@pytest.mark.parametrize("change", ["revoke", "reset", "expire", "retarget", "delete"])
def test_logout_rechecks_account_and_session_after_waiting_for_the_account_lock(client, db, world, monkeypatch, path, change):
    first, sibling, foreign = world["sessions"]
    reset_raw = None
    if change == "reset":
        proof = visitor_challenges.issue_challenge(db, "reset", account_id=world["first"],
            email="visitor.first@example.com", now=NOW)
        reset_raw = proof.raw_token
        db.commit()
    original = visitor_sessions.get_valid_session
    resolved = Event()
    def observe(session, raw):
        row = original(session, raw)
        if row is not None:
            resolved.set()
        return row
    monkeypatch.setattr(visitor_sessions, "get_valid_session", observe)
    fresh_id = None
    with Session(db.get_bind()) as blocker:
        owner = visitor_sessions.lock_verified_account(blocker, world["first"])
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(client.post, path, headers={"X-Visitor-Token": first["raw"]})
            try:
                assert resolved.wait(timeout=5)
                if change == "revoke":
                    visitor_sessions.revoke_session(blocker, account_id=world["first"], session_id=first["id"])
                elif change == "reset":
                    assert visitor_challenges.consume_challenge(blocker, reset_raw, purpose="reset", now=NOW) is not None
                    owner.hashed_password = get_password_hash("Changed!Visitor-pass-26")
                    visitor_sessions.revoke_all_sessions(blocker, account_id=world["first"])
                    fresh, _ = visitor_sessions.create_session(blocker, owner)
                    fresh_id = fresh.id
                elif change == "expire":
                    blocker.execute(update(VisitorSession).where(VisitorSession.id == first["id"])
                        .values(expires_at=NOW + timedelta(seconds=1)))
                    world["clock"][0] = NOW + timedelta(seconds=1)
                elif change == "retarget":
                    # Defensive recheck only: changing a session's account is not a product operation.
                    blocker.execute(update(VisitorSession).where(VisitorSession.id == first["id"])
                        .values(account_id=world["second"]))
                else:
                    blocker.execute(text("DELETE FROM visitor_accounts WHERE id=:id"), {"id": world["first"]})
                blocker.commit()
                response = pending.result(timeout=10)
            finally:
                blocker.rollback()
        no_store(response, 204)
    db.expire_all()
    assert db.get(VisitorSession, foreign["id"]).revoked_at is None
    if change == "delete":
        assert db.get(VisitorSession, first["id"]) is None and db.get(VisitorSession, sibling["id"]) is None
    else:
        assert db.get(VisitorSession, sibling["id"]).revoked_at == (NOW if change == "reset" else None)
        assert db.get(VisitorSession, first["id"]).revoked_at == (NOW if change in {"reset", "revoke"} else None)
        if fresh_id is not None:
            assert db.get(VisitorSession, fresh_id).revoked_at is None


def test_error_details_redact_the_opaque_header_and_never_echo_tokens(client, world):
    raw = "vst_" + "A" * 43
    response = client.get("/visitor/me", headers={"X-Visitor-Token": raw,
        "Authorization": "Bearer private-crm-canary", "Cookie": "private-cookie-canary"})
    no_store(response, 401)
    assert raw not in response.text
    assert "private-crm-canary" not in response.text and "private-cookie-canary" not in response.text
    assert raw not in str(dict(response.headers))


def test_profile_and_logout_create_no_crm_identity_subscription_or_outbox(client, db, world):
    headers = {"X-Visitor-Token": world["sessions"][0]["raw"]}
    no_store(client.get("/visitor/me", headers=headers), 200)
    no_store(client.post(LOGOUTS[1], headers=headers), 204)
    assert [db.query(table).count() for table in (User, Subscription, NotificationOutbox)] == world["before"]
    assert db.query(VisitorAccount).count() == 2
    assert db.query(VisitorSession).count() == 3
    assert db.query(VisitorChallenge).count() == 0
