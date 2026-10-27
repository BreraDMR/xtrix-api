"""Read-only registered visitor history with real grants and opaque sessions."""
from datetime import timedelta

import pytest
from sqlalchemy import event, update
from sqlalchemy.orm import Session

from app.api.core import subscription_access, visitor_sessions
from app.db.models.visit import Visit
from app.db.models.visitor_account import VisitorAccount
from app.db.models.visitor_auth import VisitorSession
from tests import test_visitor_history as base

world = base.world


def access(db, world):
    account = db.get(VisitorAccount, world['one'])
    row, raw = visitor_sessions.create_session(db, account)
    row_id, deadline = row.id, row.expires_at
    db.commit()
    return {'X-Visitor-Token': raw}, row_id, deadline


def private(response, status):
    assert response.status_code == status, response.text
    assert response.headers['cache-control'] == 'no-store'


def test_history_routes_show_exact_granted_page_detail_cursor_and_filter(client, db, world):
    headers, _, _ = access(db, world)
    base.grant(db, world)
    db.get(Visit, world['a1']).currency_snapshot = 'CZK'
    db.commit()
    response = client.get('/visitor/visits', params={'limit': 1}, headers=headers)
    private(response, 200)
    first = response.json()
    assert [row['id'] for row in first['entries']] == [world['a1']]
    assert first['entries'][0]['price'] == {'amount': '1234.56', 'currency': 'CZK'}
    cursor = first['next_cursor']
    last = client.get('/visitor/visits', params={'limit': 1, 'after_start': cursor['date_start'], 'after_id': cursor['id']}, headers=headers)
    private(last, 200)
    assert [row['id'] for row in last.json()['entries']] == [world['a2']]
    assert last.json()['next_cursor'] is None
    detail = client.get(f"/visitor/visits/{world['a1']}", headers=headers)
    private(detail, 200)
    assert detail.json() == first['entries'][0]
    assert detail.json()['start'].endswith('Z')
    assert 'private-' not in detail.text and headers['X-Visitor-Token'] not in detail.text
    filtered = client.get('/visitor/visits', params={'business_id': world['b']}, headers=headers)
    private(filtered, 200)
    assert filtered.json() == {'entries': [], 'next_cursor': None}


@pytest.mark.parametrize('path', ['/visitor/visits', '/visitor/visits/1'])
def test_history_requires_visitor_credential_even_if_crm_bearer_is_present(client, path):
    for headers in ({}, {'Authorization': 'Bearer unrelated-crm-credential'}):
        private(client.get(path, headers=headers), 401)


@pytest.mark.parametrize('key', ['a2', 'b1', 'a_other', 'solo', 'missing'])
def test_one_visit_claim_does_not_disclose_neighbor_or_foreign_detail(client, db, world, key):
    headers, _, _ = access(db, world)
    base.grant(db, world, visit=world['a1'])
    db.commit()
    visit_id = world.get(key, 987654321)
    response = client.get(f'/visitor/visits/{visit_id}', headers=headers)
    private(response, 404)
    assert 'private-' not in response.text


@pytest.mark.parametrize('params', [{'limit': 0}, {'limit': 101}, {'business_id': 0}, {'after_id': 1},
                                  {'after_start': '2026-10-27T00:00:00Z'}, {'after_start': 'invalid', 'after_id': 1}])
def test_history_pagination_rejects_partial_or_invalid_cursor_and_bounds(client, db, world, params):
    headers, _, _ = access(db, world)
    private(client.get('/visitor/visits', params=params, headers=headers), 422)


@pytest.mark.parametrize('change', ['expire', 'revoke', 'retarget'])
@pytest.mark.parametrize('detail', [False, True])
def test_history_rechecks_immutable_actor_after_the_real_projection_select(client, db, world, monkeypatch, change, detail):
    headers, session_id, deadline = access(db, world)
    base.grant(db, world)
    db.commit()
    observed = []
    engine = db.get_bind()

    def after_projection(_conn, _cursor, statement, *_args):
        if statement.lstrip().startswith('SELECT') and 'authorized_history_visits' in statement:
            observed.append(statement)
            if change == 'expire':
                monkeypatch.setattr(visitor_sessions, 'utcnow', lambda: deadline)
            else:
                with Session(engine) as writer:
                    values = {'revoked_at': deadline - timedelta(days=1)} if change == 'revoke' else {'account_id': world['two']}
                    writer.execute(update(VisitorSession).where(VisitorSession.id == session_id).values(**values))
                    writer.commit()

    event.listen(engine, 'after_cursor_execute', after_projection)
    try:
        response = client.get(f"/visitor/visits/{world['a1']}" if detail else '/visitor/visits', headers=headers)
    finally:
        event.remove(engine, 'after_cursor_execute', after_projection)
    assert len(observed) == 1
    private(response, 401)
    assert 'Salon A' not in response.text and '1234.56' not in response.text


def test_history_remains_readable_with_crm_and_mail_unavailable_without_any_writes(client, db, world, monkeypatch):
    headers, _, _ = access(db, world)
    base.grant(db, world)
    db.commit()
    def forbidden(*_args, **_kwargs):
        raise AssertionError('Visitor history entered a CRM write or subscription gate')
    monkeypatch.setattr(subscription_access, 'enforce_business_access', forbidden)
    monkeypatch.setattr(subscription_access, 'enforce_subscription_request_access', forbidden)
    statements = []
    def capture(_conn, _cursor, statement, *_args): statements.append(statement.lstrip().upper())
    engine = db.get_bind()
    event.listen(engine, 'before_cursor_execute', capture)
    try:
        response = client.get('/visitor/visits', headers=headers)
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
    private(response, 200)
    assert len(response.json()['entries']) == 2
    assert all(row['price'] is None for row in response.json()['entries'])
    assert statements and all(statement.startswith('SELECT') for statement in statements)


@pytest.mark.parametrize('locale', ['en', 'ru', 'cz'])
def test_history_errors_are_localized_private_and_do_not_disclose_scope(client, db, world, locale):
    from app.api.core.i18n import _ as translate
    headers, _, _ = access(db, world)
    headers['Accept-Language'] = locale
    response = client.get('/visitor/visits/987654321', headers=headers)
    private(response, 404)
    assert translate('visitor_history_visit_unavailable', locale) in response.text
    partial = client.get('/visitor/visits', params={'after_id': 1}, headers=headers)
    private(partial, 422)
    assert translate('visitor_history_cursor_invalid', locale) in partial.text


def test_signed_crm_access_cannot_read_visitor_history(client, db, world):
    from app.api.core.security import create_access_token
    from app.db.models.user import User
    actor = db.get(User, world['staff_a'])
    raw = create_access_token({'sub': actor.email})
    private(client.get('/visitor/visits', headers={'Authorization': 'Bearer ' + raw}), 401)
