"""Claiming a guest link adds access without changing the booked visit."""
import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.core import visitor_sessions
from app.api.core.clock import utcnow
from app.db.models.booking_manage_token import BookingManageToken
from app.db.models.visitor_account import VisitorAccount
from app.db.models.visitor_access import VisitorClaim, VisitorGrant
from app.db.models.visit import Visit
from tests.test_visitor_access_models import seed_access_world

URL = "/visitor/claims/manage"
MANAGE = "synthetic-private-guest-manage-proof"


@pytest.fixture
def claim_world(db):
    world = seed_access_world(db)
    source = db.get(BookingManageToken, world["token"])
    source.token_hash = hashlib.sha256(MANAGE.encode()).hexdigest()
    # The seed uses fixed timestamps; this route uses the real clock.
    from datetime import timedelta
    source.created_at = utcnow()
    source.expires_at = utcnow() + timedelta(days=7)
    world["session"] = visitor_sessions.create_session(db, db.get(VisitorAccount, world["one"]))[1]
    world["foreign"] = visitor_sessions.create_session(db, db.get(VisitorAccount, world["two"]))[1]
    db.commit()
    return world


def private(response, status):
    assert response.status_code == status, response.text
    assert response.headers.get("cache-control") == "no-store"
    assert "set-cookie" not in response.headers
    assert MANAGE not in response.text


def claim(client, world, **body):
    return client.post(URL, json={"token": MANAGE, **body}, headers={"X-Visitor-Token": world["session"]})


def test_claim_repeat_and_foreign_account_never_duplicate_or_transfer_visit(client, db, claim_world):
    before = db.query(Visit).count()
    source = db.get(BookingManageToken, claim_world["token"])
    expiry = source.expires_at
    private(claim(client, claim_world), 204)
    private(claim(client, claim_world), 204)
    private(client.post(URL, json={"token": MANAGE}, headers={"X-Visitor-Token": claim_world["foreign"]}), 409)
    db.expire_all()
    grant, = db.query(VisitorGrant).all()
    assert grant.account_id == claim_world["one"] and grant.scope == "visit"
    assert grant.visit_id == claim_world["visit"]
    assert db.query(VisitorClaim).count() == 1 and db.query(Visit).count() == before
    assert source.revoked_at is None and source.expires_at == expiry


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer crm-only"}, {"X-Client-Token": "legacy-only"}])
def test_only_visitor_session_can_claim(client, db, claim_world, headers):
    private(client.post(URL, json={"token": MANAGE}, headers=headers), 401)
    assert db.query(VisitorGrant).count() == db.query(VisitorClaim).count() == 0


@pytest.mark.parametrize("locale", ["en", "ru", "cz"])
def test_unknown_proof_is_private_and_localized(client, db, claim_world, locale):
    from app.api.core.i18n import _
    response = client.post(URL, json={"token": "unknown"}, headers={
        "X-Visitor-Token": claim_world["session"], "Accept-Language": locale})
    private(response, 400)
    assert response.json()["error"]["message"] == _("visitor_manage_claim_invalid", locale)
    assert db.query(VisitorGrant).count() == db.query(VisitorClaim).count() == 0


def test_body_cannot_choose_owner_or_target_and_errors_hide_proof(client, db, claim_world, monkeypatch):
    from app.api.core.config import settings
    monkeypatch.setattr(settings, "detailed_errors", True)
    for extra in ("account_id", "business_id", "client_id", "visit_id"):
        private(claim(client, claim_world, **{extra: claim_world["two"]}), 422)
    assert db.query(VisitorGrant).count() == db.query(VisitorClaim).count() == 0


def test_failed_commit_returns_no_success_and_rolls_back_grant_and_receipt(db, claim_world):
    from app.main import app
    db.execute(text("""CREATE FUNCTION review_claim_api_failure() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'synthetic claim commit failure'; RETURN NEW; END $$"""))
    db.execute(text("""CREATE CONSTRAINT TRIGGER review_claim_api_failure
        AFTER INSERT ON visitor_grants DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION review_claim_api_failure()"""))
    db.commit()
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = claim(client, claim_world)
        assert response.status_code == 500 and MANAGE not in response.text
        db.expire_all()
        assert db.query(VisitorGrant).count() == db.query(VisitorClaim).count() == 0
        assert db.get(BookingManageToken, claim_world["token"]).revoked_at is None
    finally:
        db.rollback()
        db.execute(text("DROP TRIGGER review_claim_api_failure ON visitor_grants"))
        db.execute(text("DROP FUNCTION review_claim_api_failure()"))
        db.commit()
