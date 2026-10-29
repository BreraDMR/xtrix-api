"""The visitor requests a new-contact proof through a private local-only transport."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.core import visitor_email_change_mail as mail, visitor_sessions
from app.api.core.config import settings
from app.api.core.i18n import _ as translate
from app.db.models.visitor_account import VisitorAccount
from app.db.models.visitor_auth import VisitorChallenge
from tests import test_visitor_email_change_mail as base

world = base.world
mail_world = base.mail_world
URL = '/visitor/auth/email/change/request'
CONFIRM = '/visitor/auth/email/change/confirm'


def headers(world):
    return {'X-Visitor-Token': world['sessions'][0]}


def private(response, status):
    assert response.status_code == status, response.text
    assert response.headers['cache-control'] == 'no-store'


def test_request_captures_only_after_commit_and_never_returns_raw_proof(client, db, mail_world):
    response = client.post(URL, json={'email': 'New.Contact+Tag@EXAMPLE.COM'}, headers=headers(mail_world))
    private(response, 202)
    assert set(response.json()) == {'message'}
    prepared, = mail.captured_messages()
    assert prepared.recipient == 'new.contact+tag@example.com'
    assert db.get(VisitorChallenge, prepared.challenge_id) is not None
    assert prepared.recipient not in response.text and base.raw_proof(prepared) not in response.text
    assert db.get(VisitorAccount, mail_world['one']).email_canonical == mail_world['old_email']


def test_different_target_during_cooldown_has_identical_response_without_new_proof_or_delivery(client, db, mail_world):
    first = client.post(URL, json={'email': 'first@example.com'}, headers=headers(mail_world))
    second = client.post(URL, json={'email': 'second@example.com'}, headers=headers(mail_world))
    private(first, 202)
    private(second, 202)
    assert first.json() == second.json()
    assert db.query(VisitorChallenge).count() == 1 and len(mail.captured_messages()) == 1


@pytest.mark.parametrize('mode, environment', [('disabled', 'test'), ('capture', 'production'), ('capture', 'staging')])
def test_unavailable_transport_has_no_proof_or_capture_side_effect(client, db, mail_world, monkeypatch, mode, environment):
    monkeypatch.setattr(settings, 'visitor_auth_mail_mode', mode)
    monkeypatch.setattr(settings, 'environment', environment)
    private(client.post(URL, json={'email': 'new@example.com'}, headers=headers(mail_world)), 503)
    assert db.query(VisitorChallenge).count() == 0 and mail.captured_messages() == ()


@pytest.mark.parametrize('auth', [{}, {'Authorization': 'Bearer unrelated-crm-token'}])
def test_request_cannot_use_anonymous_or_crm_authority(client, db, mail_world, auth):
    private(client.post(URL, json={'email': 'new@example.com'}, headers=auth), 401)
    assert db.query(VisitorChallenge).count() == 0 and mail.captured_messages() == ()


def test_old_but_live_session_requires_recent_sign_in_before_request(client, db, mail_world):
    mail_world['clock'][0] += timedelta(minutes=5)
    private(client.post(URL, json={'email': 'new@example.com'}, headers=headers(mail_world)), 403)
    assert db.query(VisitorChallenge).count() == 0 and mail.captured_messages() == ()
    assert visitor_sessions.get_valid_session(db, mail_world['sessions'][0]) is not None


@pytest.mark.parametrize('body', [{'email': 'invalid'}, {'email': 'new@example.com', 'account_id': 1},
                                 {'email': 'new@example.com', 'token': 'private-body-token-canary'}])
def test_request_validates_contact_and_never_accepts_a_body_actor(client, db, mail_world, body):
    response = client.post(URL, json=body, headers=headers(mail_world))
    private(response, 422)
    assert 'private-body-token-canary' not in response.text
    assert db.query(VisitorChallenge).count() == 0


@pytest.mark.parametrize('locale', ['en', 'ru', 'cz'])
def test_request_uses_localized_generic_acknowledgement_and_private_local_message(client, mail_world, locale):
    response = client.post(URL, json={'email': 'new@example.com'}, headers={**headers(mail_world), 'Accept-Language': locale})
    private(response, 202)
    assert response.json() == {'message': translate('visitor_email_change_requested', locale)}
    prepared, = mail.captured_messages()
    assert prepared.subject == {'en': 'Xtrix email change', 'ru': 'Смена почты Xtrix', 'cz': 'Změna e-mailu Xtrix'}[locale]


@pytest.mark.parametrize('target', ['owner.two@example.com', 'unknown@example.com'])
def test_address_occupancy_is_not_disclosed_by_request(client, mail_world, target):
    response = client.post(URL, json={'email': target}, headers=headers(mail_world))
    private(response, 202)
    assert target not in response.text and set(response.json()) == {'message'}


def test_request_then_confirm_changes_contact_and_logs_out_all_owner_devices(client, db, mail_world):
    private(client.post(URL, json={'email': 'new@example.com'}, headers=headers(mail_world)), 202)
    prepared, = mail.captured_messages()
    response = client.post(CONFIRM, json={'token': base.raw_proof(prepared)}, headers=headers(mail_world))
    private(response, 204)
    db.expire_all()
    assert db.get(VisitorAccount, mail_world['one']).email_canonical == 'new@example.com'
    assert all(visitor_sessions.get_valid_session(db, raw) is None for raw in mail_world['sessions'][:2])
    assert visitor_sessions.get_valid_session(db, mail_world['sessions'][2]) is not None


def test_capture_failure_returns_unavailable_but_keeps_committed_proof_and_cooldown(client, db, mail_world, monkeypatch):
    def fail_capture(*_args, **_kwargs):
        raise mail.EmailChangeMailUnavailable('private failure canary')
    monkeypatch.setattr(mail, 'capture_after_commit', fail_capture)
    first = client.post(URL, json={'email': 'new@example.com'}, headers=headers(mail_world))
    private(first, 503)
    assert 'private failure canary' not in first.text
    db.expire_all()
    assert db.query(VisitorChallenge).count() == 1 and mail.captured_messages() == ()
    second = client.post(URL, json={'email': 'new@example.com'}, headers=headers(mail_world))
    private(second, 202)
    db.expire_all()
    assert db.query(VisitorChallenge).count() == 1 and mail.captured_messages() == ()


def test_real_commit_failure_cannot_capture_an_uncommitted_request(db, mail_world):
    from app.main import app
    db.execute(text("""CREATE FUNCTION email_request_commit_failure() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'forced email request commit failure'; END $$"""))
    db.execute(text("""CREATE CONSTRAINT TRIGGER email_request_commit_failure
        AFTER INSERT ON visitor_challenges DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION email_request_commit_failure()"""))
    db.commit()
    try:
        with TestClient(app, raise_server_exceptions=False) as failing:
            response = failing.post(URL, json={'email': 'new@example.com'}, headers=headers(mail_world))
        assert response.status_code == 500
        assert 'forced email request commit failure' not in response.text
        db.expire_all()
        assert db.query(VisitorChallenge).count() == 0 and mail.captured_messages() == ()
    finally:
        db.rollback()
        db.execute(text('DROP TRIGGER email_request_commit_failure ON visitor_challenges'))
        db.execute(text('DROP FUNCTION email_request_commit_failure()'))
        db.commit()
