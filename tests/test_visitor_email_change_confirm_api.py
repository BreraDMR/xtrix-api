"""Email change confirmation uses an exact recent visitor actor and committed proof."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.core import visitor_email_change as core, visitor_sessions
from app.api.core.i18n import _ as translate
from app.db.models.visitor_account import VisitorAccount
from app.db.models.visitor_auth import VisitorChallenge, VisitorSession
from tests import test_visitor_email_change as base

world = base.world
URL = '/visitor/auth/email/change/confirm'


def private(response, status):
    assert response.status_code == status, response.text
    assert response.headers.get('cache-control') == 'no-store'
    if status == 204:
        assert response.content == b''


def headers(world, actor=0):
    return {'X-Visitor-Token': world['sessions'][actor]}


def test_confirm_commits_new_contact_and_revokes_all_owner_sessions(client, db, world):
    prepared = base.proof(db, world)
    response = client.post(URL, json={'token': prepared.raw_token}, headers=headers(world))
    private(response, 204)
    db.expire_all()
    assert db.get(VisitorAccount, world['one']).email_canonical == 'new.contact+tag@example.com'
    assert db.get(VisitorChallenge, prepared.challenge_id).consumed_at is not None
    assert all(visitor_sessions.get_valid_session(db, raw) is None for raw in world['sessions'][:2])
    assert visitor_sessions.get_valid_session(db, world['sessions'][2]) is not None
    private(client.get('/visitor/me', headers=headers(world)), 401)


@pytest.mark.parametrize('auth', [{}, {'Authorization': 'Bearer unrelated-crm-token'}])
def test_confirm_requires_separate_visitor_session(client, db, world, auth):
    prepared = base.proof(db, world)
    private(client.post(URL, json={'token': prepared.raw_token}, headers=auth), 401)
    db.expire_all()
    assert db.get(VisitorChallenge, prepared.challenge_id).consumed_at is None
    assert db.get(VisitorAccount, world['one']).email_canonical == world['old_email']


def test_foreign_actor_cannot_consume_another_accounts_email_proof(client, db, world):
    prepared = base.proof(db, world)
    private(client.post(URL, json={'token': prepared.raw_token}, headers=headers(world, 2)), 400)
    db.expire_all()
    assert db.get(VisitorChallenge, prepared.challenge_id).consumed_at is None
    assert db.get(VisitorAccount, world['one']).email_canonical == world['old_email']


@pytest.mark.parametrize('locale', ['en', 'ru', 'cz'])
def test_stale_sign_in_is_localized_but_history_session_is_not_destroyed(client, db, world, locale):
    prepared = base.proof(db, world)
    world['clock'][0] += timedelta(minutes=5)
    response = client.post(URL, json={'token': prepared.raw_token}, headers={**headers(world), 'Accept-Language': locale})
    private(response, 403)
    assert translate('visitor_recent_auth_required', locale) in response.text
    db.expire_all()
    assert db.get(VisitorChallenge, prepared.challenge_id).consumed_at is None
    assert visitor_sessions.get_valid_session(db, world['sessions'][0]) is not None


def test_occupied_contact_is_generic_conflict_and_whole_operation_rolls_back(client, db, world):
    target = db.get(VisitorAccount, world['two']).email_canonical
    prepared = base.proof(db, world, target)
    response = client.post(URL, json={'token': prepared.raw_token}, headers=headers(world))
    private(response, 409)
    assert target not in response.text and prepared.raw_token not in response.text
    db.expire_all()
    assert db.get(VisitorAccount, world['one']).email_canonical == world['old_email']
    assert db.get(VisitorChallenge, prepared.challenge_id).consumed_at is None
    assert all(db.get(VisitorSession, ident).revoked_at is None for ident in world['session_ids'])


@pytest.mark.parametrize('extra', [{'account_id': 1}, {'email': 'bypass@example.com'}])
def test_confirm_does_not_accept_actor_or_target_from_body(client, db, world, extra):
    prepared = base.proof(db, world)
    response = client.post(URL, json={'token': prepared.raw_token, **extra}, headers=headers(world))
    private(response, 422)
    assert prepared.raw_token not in response.text
    assert db.get(VisitorChallenge, prepared.challenge_id).consumed_at is None


def test_invalid_proof_is_private_and_does_not_revoke_the_actor(client, db, world):
    response = client.post(URL, json={'token': 'vch_' + 'A' * 43}, headers=headers(world))
    private(response, 400)
    assert visitor_sessions.get_valid_session(db, world['sessions'][0]) is not None


def test_confirmation_does_not_depend_on_mail_or_crm_payment(client, db, world, monkeypatch):
    from app.api.core.config import settings
    from app.api.core import subscription_access
    def forbidden(*_args, **_kwargs):
        raise AssertionError('Email confirmation entered a CRM billing gate')
    monkeypatch.setattr(settings, 'visitor_auth_mail_mode', 'disabled')
    monkeypatch.setattr(subscription_access, 'enforce_business_access', forbidden)
    monkeypatch.setattr(subscription_access, 'enforce_subscription_request_access', forbidden)
    prepared = base.proof(db, world)
    private(client.post(URL, json={'token': prepared.raw_token}, headers=headers(world)), 204)


def test_deferred_commit_failure_does_not_confirm_or_revoke_anything(db, world):
    from app.main import app
    prepared = base.proof(db, world)
    db.execute(text("""CREATE FUNCTION email_http_commit_failure() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'forced email confirmation commit failure'; END $$"""))
    db.execute(text("""CREATE CONSTRAINT TRIGGER email_http_commit_failure
        AFTER UPDATE ON visitor_accounts DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION email_http_commit_failure()"""))
    db.commit()
    try:
        with TestClient(app, raise_server_exceptions=False) as failing:
            response = failing.post(URL, json={'token': prepared.raw_token}, headers=headers(world))
        assert response.status_code == 500
        assert prepared.raw_token not in response.text and world['old_email'] not in response.text
        db.expire_all()
        assert db.get(VisitorAccount, world['one']).email_canonical == world['old_email']
        assert db.get(VisitorChallenge, prepared.challenge_id).consumed_at is None
        assert all(db.get(VisitorSession, ident).revoked_at is None for ident in world['session_ids'])
    finally:
        db.rollback()
        db.execute(text('DROP TRIGGER email_http_commit_failure ON visitor_accounts'))
        db.execute(text('DROP FUNCTION email_http_commit_failure()'))
        db.commit()
