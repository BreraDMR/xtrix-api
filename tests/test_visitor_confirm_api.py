"""Visitor confirmation uses the real command, transaction and HTTP error handlers."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app.api.core import subscription_access, visitor_confirm
from app.api.core.i18n import _
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.subscription import Subscription
from app.db.models.visit import Visit
from app.db.models.visitor_access import VisitorGrant
from app.main import app
from tests import test_visitor_confirm_core as core


@pytest.fixture
def world(db, monkeypatch):
    result = core.world.__wrapped__(db, monkeypatch)
    monkeypatch.setattr(subscription_access, 'utcnow', lambda: result['clock'][0])
    return result


def url(world):
    return f"/visitor/visits/{world['visit']}/confirm"


def headers(world, locale='ru'):
    return {'X-Visitor-Token': world['raw'], 'Accept-Language': locale}


def private(response, status):
    assert response.status_code == status, response.text
    assert response.headers['cache-control'] == 'no-store'


def test_committed_confirmation_repeat_and_history(client, db, world):
    before = core.snapshot(db, world)
    db.rollback()
    private(client.post(url(world), headers=headers(world)), 204)
    with Session(db.get_bind()) as observer:
        after = core.snapshot(observer, world)
        first = observer.get(Visit, world['visit']).client_confirmed_at
    assert first == world['clock'][0]
    assert {key for key in before[0] if before[0][key] != after[0][key]} == {'client_confirmed_at'}
    assert before[1] == after[1]
    world['clock'][0] += timedelta(hours=1)
    response = client.post(url(world), headers=headers(world))
    private(response, 204)
    assert response.content == b'' and 'set-cookie' not in response.headers
    with Session(db.get_bind()) as observer:
        assert observer.get(Visit, world['visit']).client_confirmed_at == first
    response = client.get(f"/visitor/visits/{world['visit']}", headers=headers(world))
    private(response, 200)
    assert response.json()['confirmed_at'] == first.isoformat() + 'Z'


@pytest.mark.parametrize('credential', ['missing', 'crm', 'guest', 'invalid', 'expired'])
def test_only_visitor_session_can_confirm(client, db, world, credential):
    supplied = {}
    if credential == 'crm':
        supplied = {'Authorization': 'Bearer unrelated-crm-credential'}
    elif credential == 'guest':
        supplied = {'X-Booking-Token': world['raw']}
    elif credential == 'invalid':
        supplied = {'X-Visitor-Token': 'invalid'}
    elif credential == 'expired':
        world['clock'][0] += timedelta(days=31)
        supplied = headers(world)
    private(client.post(url(world), headers=supplied), 401)
    with Session(db.get_bind()) as observer:
        assert observer.get(Visit, world['visit']).client_confirmed_at is None


@pytest.mark.parametrize('change', ['foreign', 'revoked', 'visit_deleted', 'client_deleted',
                                   'business_deleted', 'retargeted', 'unknown'])
def test_unavailable_targets_share_404_before_billing(client, db, world, monkeypatch, change):
    supplied = headers(world)
    target_url = url(world)
    if change == 'foreign':
        supplied['X-Visitor-Token'] = world['other_raw']
    elif change == 'revoked':
        db.get(VisitorGrant, world['grant']).revoked_at = world['clock'][0]
    elif change == 'retargeted':
        db.get(Visit, world['visit']).client_id = world['other_client']
    elif change == 'unknown':
        target_url = '/visitor/visits/987654321/confirm'
    else:
        model, key = {'visit_deleted': (Visit, 'visit'), 'client_deleted': (Client, 'client'),
                      'business_deleted': (Business, 'business')}[change]
        db.get(model, world[key]).deleted_at = world['clock'][0]
    db.commit()
    def forbidden(*args, **kwargs):
        raise AssertionError('Denied target entered billing')
    monkeypatch.setattr(subscription_access, 'enforce_business_access', forbidden)
    response = client.post(target_url, headers=supplied)
    private(response, 404)
    assert response.json()['error']['message'] == _('visitor_history_visit_unavailable', 'ru')
    assert world['raw'] not in response.text


@pytest.mark.parametrize('locale', ['en', 'ru', 'cz'])
@pytest.mark.parametrize('reason', ['started', 'cancelled', 'unpaid'])
def test_localized_policy_denial_and_history_stays_readable(client, db, world, locale, reason):
    if reason == 'started':
        world['clock'][0] += timedelta(days=3)
        code = 'booking_confirm_started'
    elif reason == 'cancelled':
        db.get(Visit, world['visit']).status = 'cancelled'
        code = 'booking_confirm_cancelled'
    else:
        db.get(Subscription, world['sub']).status = 'cancelled'
        code = 'visitor_actions_unavailable'
    db.commit()
    response = client.post(url(world), headers=headers(world, locale))
    private(response, 409)
    assert response.json()['error']['message'] == _(code, locale)
    assert response.json()['error']['detail']['code'] == code
    private(client.get(f"/visitor/visits/{world['visit']}", headers=headers(world)), 200)


def test_real_nowait_busy_is_private_and_releases_account_lock(client, db, world):
    db.rollback()
    with Session(db.get_bind()) as blocker:
        blocker.execute(text('SELECT id FROM visits WHERE id=:id FOR UPDATE'), {'id': world['visit']})
        response = client.post(url(world), headers=headers(world))
        private(response, 409)
        assert response.json()['error']['message'] == _('visitor_action_retry', 'ru')
        with Session(db.get_bind()) as observer:
            observer.execute(text('SELECT id FROM visitor_accounts WHERE id=:id FOR UPDATE NOWAIT'),
                             {'id': world['account']})
    private(client.post(url(world), headers=headers(world)), 204)


def test_real_deferred_commit_failure_rolls_back_confirmation(db, world):
    db.execute(text("""CREATE FUNCTION reject_http_confirmation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'synthetic deferred confirmation failure'; RETURN NEW; END $$"""))
    db.execute(text('''CREATE CONSTRAINT TRIGGER reject_http_confirmation AFTER UPDATE OF client_confirmed_at ON visits
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION reject_http_confirmation()'''))
    db.commit()
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(url(world), headers=headers(world))
        private(response, 500)
        assert world['raw'] not in response.text
        with Session(db.get_bind()) as observer:
            assert observer.get(Visit, world['visit']).client_confirmed_at is None
    finally:
        db.rollback()
        db.execute(text('DROP TRIGGER reject_http_confirmation ON visits'))
        db.execute(text('DROP FUNCTION reject_http_confirmation()'))
        db.commit()


def test_internal_errors_remain_500_and_private(db, world, monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError('synthetic internal failure')
    monkeypatch.setattr(visitor_confirm, 'confirm_visit', fail)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(url(world), headers=headers(world))
    private(response, 500)
    assert world['raw'] not in response.text


@pytest.mark.parametrize('visit_id', ['0', '-1', 'invalid'])
def test_positive_path_validation_is_private(client, world, visit_id):
    private(client.post(f'/visitor/visits/{visit_id}/confirm', headers=headers(world)), 422)


def test_deadline_after_actual_update_returns_conflict_and_whole_rollback(client, db, world):
    seen = []
    def after(_conn, _cursor, statement, *args):
        if statement.startswith('UPDATE visits SET client_confirmed_at'):
            seen.append(statement)
            world['clock'][0] += timedelta(days=3)
    event.listen(db.get_bind(), 'after_cursor_execute', after)
    try:
        response = client.post(url(world), headers=headers(world))
    finally:
        event.remove(db.get_bind(), 'after_cursor_execute', after)
    private(response, 409)
    assert seen and response.json()['error']['message'] == _('booking_confirm_started', 'ru')
    with Session(db.get_bind()) as observer:
        assert observer.get(Visit, world['visit']).client_confirmed_at is None
