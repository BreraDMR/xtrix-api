"""Only the verified invite recipient can attach an entire client history."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, text

from app.api.core import visitor_history, visitor_sessions
from app.api.core.clock import utcnow
from app.db.models.client_business_relation import ClientBusinessRelation
from app.db.models.subscription import Subscription
from app.db.models.user import User
from app.db.models.visit import Visit
from app.db.models.visitor_access import VisitorClaim, VisitorGrant
from app.db.models.visitor_account import VisitorAccount
from tests import test_visitor_client_invite_issue_api as issue_http_tests

invitation_world = issue_http_tests.invitation_world

URL = "/visitor/claims/client-invite"


@pytest.fixture
def accept_world(client, db, invitation_world):
    world = invitation_world
    owner = db.get(VisitorAccount, world["one"])
    response = client.post(world["url"], json={"email": owner.email_canonical}, headers=world["headers"])
    assert response.status_code == 201, response.text
    world["proof"] = response.json()["token"]
    world["visitor"] = visitor_sessions.create_session(db, owner)[1]
    world["foreign"] = visitor_sessions.create_session(db, db.get(VisitorAccount, world["two"]))[1]
    db.commit()
    return world


def accept(client, world, token=None):
    return client.post(URL, json={"token": token or world["proof"]}, headers={"X-Visitor-Token": world["visitor"]})


def private(response, status):
    assert response.status_code == status, response.text
    assert response.headers.get("cache-control") == "no-store"
    assert "set-cookie" not in response.headers
    assert "vci_" not in response.text


def test_recipient_attaches_exact_client_history_even_after_crm_payment_stops(client, db, accept_world):
    world = accept_world
    own = Visit(business_id=world["business"], client_id=world["client"], specialist_id=world["staff"],
                date_start=utcnow() - timedelta(days=2), status="cancelled")
    foreign = Visit(business_id=world["other"], client_id=world["client"], specialist_id=world["staff"],
                    date_start=utcnow() - timedelta(days=3), status="cancelled")
    db.add_all([own, foreign])
    db.query(Subscription).update({"status": "cancelled"})
    db.commit()
    private(accept(client, world), 204)
    grant, = db.query(VisitorGrant).all()
    assert grant.scope == "client" and grant.visit_id is None
    assert (grant.account_id, grant.business_id, grant.client_id) == (world["one"], world["business"], world["client"])
    ids = {entry.id for entry in visitor_history.list_visits(db, world["one"]).entries}
    assert ids == {world["visit"], own.id} and foreign.id not in ids
    assert db.query(VisitorClaim).one().consumed_at is not None
    assert db.query(VisitorAccount).count() == 2


def test_wrong_recipient_rolls_back_and_correct_recipient_can_retry(client, db, accept_world):
    world = accept_world
    private(client.post(URL, json={"token": world["proof"]}, headers={"X-Visitor-Token": world["foreign"]}), 400)
    assert db.query(VisitorClaim).one().consumed_at is None and db.query(VisitorGrant).count() == 0
    private(accept(client, world), 204)


def test_owned_spent_replay_does_not_need_old_email_issuer_membership_or_ttl(client, db, accept_world, monkeypatch):
    world = accept_world
    private(accept(client, world), 204)
    grant_id = db.query(VisitorGrant).one().id
    db.get(VisitorAccount, world["one"]).email_canonical = "new-verified@example.com"
    db.get(User, world["staff"]).deleted_at = utcnow()
    db.execute(delete(ClientBusinessRelation).where(ClientBusinessRelation.business_id == world["business"]))
    from app.api.core import visitor_client_invite_accept
    future = utcnow() + timedelta(hours=1)
    monkeypatch.setattr(visitor_client_invite_accept, "utcnow", lambda: future)
    monkeypatch.setattr(visitor_sessions, "utcnow", lambda: future)
    world["visitor"] = visitor_sessions.create_session(db, db.get(VisitorAccount, world["one"]))[1]
    db.commit()
    private(accept(client, world), 204)
    assert db.query(VisitorGrant).one().id == grant_id
    db.query(VisitorGrant).update({"revoked_at": utcnow()})
    db.commit()
    private(accept(client, world), 400)
    assert db.query(VisitorGrant).one().revoked_at is not None


def test_foreign_visit_access_prevents_client_wide_takeover(client, db, accept_world):
    world = accept_world
    db.add(VisitorGrant(account_id=world["two"], business_id=world["business"], client_id=world["client"],
                        scope="visit", visit_id=world["visit"]))
    db.commit()
    private(accept(client, world), 409)
    assert db.query(VisitorClaim).one().consumed_at is None
    assert db.query(VisitorGrant).one().account_id == world["two"]


@pytest.mark.parametrize("headers", [{}, "crm"])
def test_only_visitor_credentials_authenticate_accept(client, db, accept_world, headers):
    if headers == "crm":
        headers = accept_world["headers"]
    private(client.post(URL, json={"token": accept_world["proof"]}, headers=headers), 401)
    assert db.query(VisitorGrant).count() == 0


def test_request_cannot_choose_account_client_or_business_and_hides_token(client, db, accept_world, monkeypatch):
    from app.api.core.config import settings
    monkeypatch.setattr(settings, "detailed_errors", True)
    for name in ("account_id", "client_id", "business_id"):
        private(client.post(URL, json={"token": accept_world["proof"], name: 123},
                            headers={"X-Visitor-Token": accept_world["visitor"]}), 422)
    assert db.query(VisitorGrant).count() == 0


@pytest.mark.parametrize("locale", ["en", "ru", "cz"])
def test_invalid_invitation_is_private_and_localized(client, db, accept_world, locale):
    from app.api.core.i18n import _
    response = client.post(URL, json={"token": "not-a-private-invite"}, headers={
        "X-Visitor-Token": accept_world["visitor"], "Accept-Language": locale})
    private(response, 400)
    assert response.json()["error"]["message"] == _("visitor_client_invite_invalid", locale)


def test_failed_accept_commit_restores_unconsumed_invite_and_no_grant(db, accept_world):
    from app.main import app
    db.execute(text("""CREATE FUNCTION accept_http_commit_failure() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'synthetic accept commit failure'; RETURN NEW; END $$"""))
    db.execute(text("""CREATE CONSTRAINT TRIGGER accept_http_commit_failure
        AFTER INSERT ON visitor_grants DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION accept_http_commit_failure()"""))
    db.commit()
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = accept(client, accept_world)
        assert response.status_code == 500 and accept_world["proof"] not in response.text
        db.expire_all()
        assert db.query(VisitorGrant).count() == 0 and db.query(VisitorClaim).one().consumed_at is None
    finally:
        db.rollback()
        db.execute(text("DROP TRIGGER accept_http_commit_failure ON visitor_grants"))
        db.execute(text("DROP FUNCTION accept_http_commit_failure()"))
        db.commit()
