"""A reset proof changes only its visitor's password and revokes old sessions."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.core import visitor_challenges, visitor_passwords, visitor_sessions
from app.api.core.clock import utcnow
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.user import User
from app.db.models.visitor_account import VisitorAccount, VisitorGoogleIdentity
from app.db.models.visitor_auth import VisitorChallenge, VisitorSession

URL = "/visitor/auth/password/reset/confirm"
OLD = "Prior!Visitor-68-Key"
NEW = "New!Visitor-59-Pass"


@pytest.fixture
def reset_account(db):
    account = VisitorAccount(email_canonical="reset.owner@example.com", email_verified_at=utcnow(),
                             hashed_password=visitor_passwords.hash_password(OLD))
    other = VisitorAccount(email_canonical="other@example.com", email_verified_at=utcnow())
    crm = User(full_name="Synthetic CRM owner", email=account.email_canonical,
               hashed_password="unchanged-crm-password", role="specialist")
    db.add_all([account, other, crm])
    db.flush()
    db.add(VisitorGoogleIdentity(subject="preserved-google-reset-subject", account_id=account.id))
    sessions = [visitor_sessions.create_session(db, owner)[1] for owner in (account, account, other)]
    result = {"id": account.id, "other_id": other.id, "crm_id": crm.id,
              "hash": account.hashed_password, "sessions": sessions}
    db.commit()
    return result


def issue(db, reset_account, purpose="reset"):
    args = {"email": "reset.owner@example.com"} if purpose in {"reset", "register", "email_change"} else {"origin": "https://visitor.example"}
    if purpose in {"reset", "email_change", "google_link"}:
        args["account_id"] = reset_account["id"]
    proof = visitor_challenges.issue_challenge(db, purpose, **args)
    raw, row_id = proof.raw_token, proof.row.id
    db.commit()
    return raw, row_id


def private(response, status):
    assert response.status_code == status, response.text
    assert response.headers.get("cache-control") == "no-store"
    assert "set-cookie" not in response.headers


def unchanged(db, reset_account, proofs):
    db.expire_all()
    assert db.get(VisitorAccount, reset_account["id"]).hashed_password == reset_account["hash"]
    assert all(db.get(VisitorChallenge, proof).consumed_at is None for proof in proofs)
    assert all(visitor_sessions.get_valid_session(db, raw) is not None for raw in reset_account["sessions"])
    assert db.query(VisitorSession).count() == 3


def test_reset_revokes_old_sessions_without_login_or_other_identity_changes(client, db, reset_account):
    raw, proof_id = issue(db, reset_account)
    _, sibling_id = issue(db, reset_account)
    before_outbox = db.query(NotificationOutbox).count()
    response = client.post(URL, json={"token": raw, "new_password": NEW})
    private(response, 204)
    assert not response.content
    db.expire_all()
    account = db.get(VisitorAccount, reset_account["id"])
    assert visitor_passwords.verify_password(NEW, account.hashed_password)
    assert not visitor_passwords.verify_password(OLD, account.hashed_password)
    assert db.get(VisitorChallenge, proof_id).consumed_at is not None
    assert db.get(VisitorChallenge, sibling_id).consumed_at is not None
    assert db.query(VisitorSession).count() == 3
    assert all(visitor_sessions.get_valid_session(db, token) is None for token in reset_account["sessions"][:2])
    assert visitor_sessions.get_valid_session(db, reset_account["sessions"][2]) is not None
    assert db.query(VisitorGoogleIdentity).one().subject == "preserved-google-reset-subject"
    assert db.get(User, reset_account["crm_id"]).hashed_password == "unchanged-crm-password"
    assert db.query(NotificationOutbox).count() == before_outbox
    private(client.post(URL, json={"token": raw, "new_password": OLD}), 400)
    private(client.post("/visitor/auth/login", json={"email": account.email_canonical, "password": OLD}), 401)
    logged_in = client.post("/visitor/auth/login", json={"email": account.email_canonical, "password": NEW})
    private(logged_in, 200)
    assert visitor_sessions.get_valid_session(db, logged_in.json()["session_token"]).account_id == account.id


def test_weak_password_rolls_back_all_proofs_and_recipient_can_retry(client, db, reset_account):
    raw, proof_id = issue(db, reset_account)
    _, sibling_id = issue(db, reset_account)
    private(client.post(URL, json={"token": raw, "new_password": "short"}), 422)
    unchanged(db, reset_account, [proof_id, sibling_id])
    private(client.post(URL, json={"token": raw, "new_password": NEW}), 204)


@pytest.mark.parametrize("purpose", ["register", "email_change", "google_login", "google_link"])
def test_other_purposes_cannot_reset_password(client, db, reset_account, purpose):
    raw, proof_id = issue(db, reset_account, purpose)
    private(client.post(URL, json={"token": raw, "new_password": NEW}), 400)
    unchanged(db, reset_account, [proof_id])


@pytest.mark.parametrize("body", [
    {"token": "synthetic-private-reset", "new_password": NEW, "account_id": 1},
    {"token": "synthetic-private-reset", "new_password": NEW, "email": "other@example.com"},
    {"token": "synthetic-private-reset", "new_password": True},
    {"token": "synthetic-private-reset", "new_password": NEW * 8},
    {"token": "synthetic-private-reset" * 30, "new_password": NEW},
    {"token": "", "new_password": NEW},
])
def test_validation_hides_credentials_and_rejects_caller_identity(client, db, reset_account, monkeypatch, body):
    from app.api.core.config import settings
    monkeypatch.setattr(settings, "detailed_errors", True)
    response = client.post(URL, json=body)
    private(response, 422)
    assert NEW not in response.text and "synthetic-private-reset" not in response.text
    unchanged(db, reset_account, [])


@pytest.mark.parametrize("locale", ["en", "ru", "cz"])
def test_invalid_proof_is_generic_and_localized(client, db, reset_account, locale):
    from app.api.core.i18n import _
    response = client.post(URL, json={"token": "synthetic-private-reset", "new_password": NEW},
                           headers={"Accept-Language": locale})
    private(response, 400)
    assert response.json()["error"]["message"] == _("visitor_password_reset_invalid", locale)
    assert "synthetic-private-reset" not in response.text and NEW not in response.text
    unchanged(db, reset_account, [])


def test_deferred_commit_failure_returns_no_success_and_restores_all_state(db, reset_account):
    from app.main import app
    raw, proof_id = issue(db, reset_account)
    _, sibling_id = issue(db, reset_account)
    db.execute(text("""CREATE FUNCTION review_reset_api_failure() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'synthetic reset API commit failure'; RETURN NEW; END $$"""))
    db.execute(text("""CREATE CONSTRAINT TRIGGER review_reset_api_failure
        AFTER UPDATE ON visitor_accounts DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION review_reset_api_failure()"""))
    db.commit()
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(URL, json={"token": raw, "new_password": NEW})
        assert response.status_code == 500
        assert "set-cookie" not in response.headers
        assert raw not in response.text and NEW not in response.text
        unchanged(db, reset_account, [proof_id, sibling_id])
    finally:
        db.rollback()
        db.execute(text("DROP TRIGGER review_reset_api_failure ON visitor_accounts"))
        db.execute(text("DROP FUNCTION review_reset_api_failure()"))
        db.commit()
