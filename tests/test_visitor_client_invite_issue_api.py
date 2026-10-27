"""CRM staff may prepare a recipient-bound link only with a current credential."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib
from threading import Event
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app.api.core.clock import utcnow
from app.db.models.subscription import Subscription
from app.db.models.subscription_plan import SubscriptionPlan
from app.db.models.user import User
from app.db.models.visitor_access import VisitorClaim, VisitorGrant
from app.db.models.visitor_account import VisitorAccount
from tests.test_visitor_access_models import seed_access_world


@pytest.fixture
def invitation_world(db, headers_for):
    world = seed_access_world(db)
    plan = SubscriptionPlan(code="invite_http_crm", name="Synthetic CRM", period_days=30, price=200)
    db.add(plan)
    db.flush()
    db.add(Subscription(user_id=world["staff"], plan_id=plan.id, status="active",
                        current_period_end=utcnow() + timedelta(days=30), is_trial=False))
    db.commit()
    world["headers"] = headers_for(db.get(User, world["staff"]))
    world["url"] = f'/clients/{world["client"]}/visitor-invitations'
    return world


def private(response, code):
    assert response.status_code == code, response.text
    assert response.headers.get("cache-control") == "no-store"
    assert "set-cookie" not in response.headers


def test_issue_returns_only_committed_private_proof_without_creating_identity_or_grant(client, db, invitation_world):
    world = invitation_world
    before = (db.query(VisitorAccount).count(), db.query(VisitorGrant).count())
    response = client.post(world["url"], json={"email": " New.Recipient+Tag@EXAMPLE.COM "}, headers=world["headers"])
    private(response, 201)
    body = response.json()
    assert set(body) == {"token", "expires_at"} and body["expires_at"].endswith("Z")
    assert body["token"].startswith("vci_") and len(body["token"]) == 47
    proof, = db.query(VisitorClaim).all()
    assert proof.proof_hash == hashlib.sha256(body["token"].encode()).hexdigest()
    assert proof.recipient_email == "new.recipient+tag@example.com"
    assert (proof.business_id, proof.client_id, proof.issued_by_user_id) == (world["business"], world["client"], world["staff"])
    assert proof.consumed_at is None and before == (db.query(VisitorAccount).count(), db.query(VisitorGrant).count())


@pytest.mark.parametrize("headers", [{}, {"X-Visitor-Token": "vst_" + "A" * 43}])
def test_visitor_or_anonymous_request_cannot_issue(client, db, invitation_world, headers):
    private(client.post(invitation_world["url"], json={"email": "recipient@example.com"}, headers=headers), 401)
    assert db.query(VisitorClaim).count() == 0


@pytest.mark.parametrize("extra", ["business_id", "client_id", "account_id", "issuer_user_id"])
def test_body_cannot_choose_scope_or_actor(client, db, invitation_world, extra):
    world = invitation_world
    private(client.post(world["url"], json={"email": "recipient@example.com", extra: world["other"]}, headers=world["headers"]), 422)
    assert db.query(VisitorClaim).count() == 0


def test_current_billing_is_required_and_foreign_client_is_not_invited(client, db, invitation_world):
    world = invitation_world
    private(client.post('/clients/99999999/visitor-invitations', json={"email": "recipient@example.com"}, headers=world["headers"]), 403)
    db.query(Subscription).update({"status": "cancelled"})
    db.commit()
    private(client.post(world["url"], json={"email": "recipient@example.com"}, headers=world["headers"]), 402)
    assert db.query(VisitorClaim).count() == 0


def test_revoked_crm_credential_after_real_user_wait_cannot_publish_a_link(client, db, invitation_world):
    world, engine = invitation_world, db.get_bind()
    waiting, seen = Event(), {}
    def observe(conn, _cursor, statement, *_args):
        if "FROM users" in statement and "FOR UPDATE" in statement:
            seen["pid"] = conn.connection.driver_connection.get_backend_pid()
            waiting.set()
    db.rollback()
    with Session(engine) as holder:
        issuer = holder.query(User).filter_by(id=world["staff"]).with_for_update().one()
        holder_pid = holder.execute(text("SELECT pg_backend_pid()")).scalar_one()
        event.listen(engine, "before_cursor_execute", observe)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(client.post, world["url"], json={"email": "recipient@example.com"}, headers=world["headers"])
                try:
                    assert waiting.wait(timeout=5)
                    end = time.monotonic() + 5
                    with engine.connect() as observer:
                        while time.monotonic() < end:
                            if holder_pid in observer.execute(text("SELECT pg_blocking_pids(:pid)"), {"pid": seen["pid"]}).scalar_one():
                                break
                            time.sleep(0.01)
                        else:
                            pytest.fail("PostgreSQL did not confirm User lock wait")
                    issuer.tokens_valid_after = utcnow() + timedelta(milliseconds=1)
                    holder.commit()
                    response = future.result(timeout=10)
                finally:
                    holder.rollback()
        finally:
            event.remove(engine, "before_cursor_execute", observe)
    private(response, 401)
    assert db.query(VisitorClaim).count() == 0


def test_failed_commit_never_returns_or_persists_the_prepared_link(db, invitation_world):
    from app.main import app
    db.execute(text("""CREATE FUNCTION invite_http_commit_failure() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'synthetic invitation commit failure'; RETURN NEW; END $$"""))
    db.execute(text("""CREATE CONSTRAINT TRIGGER invite_http_commit_failure
        AFTER INSERT ON visitor_claims DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION invite_http_commit_failure()"""))
    db.commit()
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(invitation_world["url"], json={"email": "recipient@example.com"}, headers=invitation_world["headers"])
        assert response.status_code == 500 and "vci_" not in response.text
        assert db.query(VisitorClaim).count() == 0
    finally:
        db.rollback()
        db.execute(text("DROP TRIGGER invite_http_commit_failure ON visitor_claims"))
        db.execute(text("DROP FUNCTION invite_http_commit_failure()"))
        db.commit()
