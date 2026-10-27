"""Fixed category boundaries travel through public cursors without widening scope."""
from datetime import timedelta, timezone

import pytest
from sqlalchemy import event, text, update

from app.api.core import visitor_history as core, visitor_history_views as views
from app.db.models.visit import Visit
from app.schemas.visitor_history import VisitorHistoryCursor
from tests import test_visitor_history as base
from tests.test_visitor_history_api import access, private
from tests.test_visitor_history_categories import stored_visit

world = base.world
NOW = base.NOW


def test_http_category_filters_actual_rows_before_projection(client, db, world):
    headers, _, _ = access(db, world)
    base.grant(db, world)
    db.commit()
    response = client.get('/visitor/visits', params={'category': 'past', 'as_of': NOW.isoformat()}, headers=headers)
    private(response, 200)
    assert [row['id'] for row in response.json()['entries']] == [world['a2']]


def test_http_partition_null_future_completed_money_and_foreign_business(client, db, world):
    headers, _, _ = access(db, world)
    base.grant(db, world)
    null = stored_visit(db, world, NOW, None)
    completed = stored_visit(db, world, NOW + timedelta(hours=2), 'completed')
    cancelled = stored_visit(db, world, NOW + timedelta(hours=3), 'cancelled')
    unknown = stored_visit(db, world, NOW + timedelta(hours=4), 'unknown')
    db.execute(update(Visit).where(Visit.id == world['a1']).values(currency_snapshot='CZK'))
    db.commit()
    expected = {'upcoming': {world['a1'], null, unknown}, 'past': {world['a2'], completed}, 'cancelled': {cancelled}}
    for category, ids in expected.items():
        response = client.get('/visitor/visits', params={'category': category, 'as_of': NOW.isoformat(), 'business_id': world['a']}, headers=headers)
        private(response, 200)
        rows = response.json()['entries']
        assert {row['id'] for row in rows} == ids
        assert 'private-' not in response.text and 'client_id' not in response.text
        for row in rows:
            assert row['price'] == ({'amount': '1234.56', 'currency': 'CZK'} if row['id'] == world['a1'] else None)
            assert 'end' in row and 'status' in row
    response = client.get('/visitor/visits', params={'category': 'cancelled', 'as_of': NOW.isoformat(), 'business_id': world['b']}, headers=headers)
    private(response, 200)
    assert response.json() == {'entries': [], 'next_cursor': None}


def test_http_next_cursor_pins_first_clock_when_time_moves(client, db, world, monkeypatch):
    headers, _, _ = access(db, world)
    base.grant(db, world)
    second = stored_visit(db, world, NOW + timedelta(hours=2), 'planned')
    db.commit()
    calls = []
    wall = [NOW]
    def clock():
        calls.append(wall[0])
        return wall[0]
    monkeypatch.setattr(views, 'utcnow', clock)
    monkeypatch.setattr(core, 'utcnow', lambda: pytest.fail('View must pass an explicit boundary'))
    response = client.get('/visitor/visits', params={'category': 'upcoming', 'limit': 1}, headers=headers)
    private(response, 200)
    first = response.json()
    assert [r['id'] for r in first['entries']] == [world['a1']]
    assert len(calls) == 1
    cursor = first['next_cursor']
    assert cursor['as_of'] == '2026-10-26T12:00:00Z'
    wall[0] = NOW + timedelta(days=2)
    params = {'category': 'upcoming', 'limit': 1, 'after_start': cursor['date_start'], 'after_id': cursor['id'], 'as_of': cursor['as_of']}
    last = client.get('/visitor/visits', params=params, headers=headers)
    private(last, 200)
    assert [r['id'] for r in last.json()['entries']] == [second]
    assert last.json()['next_cursor'] is None
    assert calls == [NOW]


@pytest.mark.parametrize('locale', ['en', 'ru', 'cz'])
def test_nonall_continuation_requires_boundary_localized_private(client, db, world, locale):
    from app.api.core.i18n import _ as translate
    headers, _, _session = access(db, world)
    headers['Accept-Language'] = locale
    response = client.get('/visitor/visits', params={'category': 'upcoming', 'after_start': NOW.isoformat(), 'after_id': 1}, headers=headers)
    private(response, 422)
    assert translate('visitor_history_cursor_invalid', locale) in response.text


@pytest.mark.parametrize('params', [{'category': 'future'}, {'category': ''}, {'as_of': 'bad'},
                                  {'category': 'past', 'after_id': 1, 'as_of': NOW.isoformat()},
                                  {'category': 'cancelled', 'after_start': NOW.isoformat(), 'as_of': NOW.isoformat()}])
def test_http_category_and_cursor_input_validation_is_private(client, db, world, params):
    headers, _, _ = access(db, world)
    private(client.get('/visitor/visits', params=params, headers=headers), 422)


def test_views_schema_cursor_utc_boundary_and_explicit_matching_or_mismatch(db, world):
    base.grant(db, world)
    db.commit()
    offset = (NOW + timedelta(hours=2)).replace(tzinfo=timezone(timedelta(hours=2)))
    cursor = VisitorHistoryCursor(date_start=NOW + timedelta(days=2), id=world['b1'], as_of=offset)
    page = views.list_visits(db, world['one'], category='upcoming', cursor=cursor)
    assert base.ids(page) == [world['a1']]
    assert page == views.list_visits(db, world['one'], category='upcoming', cursor=cursor, as_of=NOW)
    with pytest.raises(ValueError):
        views.list_visits(db, world['one'], category='upcoming', cursor=cursor, as_of=NOW + timedelta(seconds=1))
    core_cursor = core.VisitorHistoryCursor(NOW + timedelta(days=2), world['b1'])
    assert page == views.list_visits(db, world['one'], category='upcoming', cursor=core_cursor, as_of=NOW)


@pytest.mark.parametrize('schema', [True, False])
def test_nonall_views_cursor_without_boundary_is_rejected_before_sql(db, schema, monkeypatch):
    cursor = VisitorHistoryCursor(date_start=NOW, id=1) if schema else core.VisitorHistoryCursor(NOW, 1)
    monkeypatch.setattr(db, 'execute', lambda *_a, **_k: pytest.fail('Missing boundary reached SQL'))
    with pytest.raises(ValueError):
        views.list_visits(db, 1, category='past', cursor=cursor)


def test_default_all_old_two_field_cursor_and_nullable_response_are_preserved(client, db, world):
    headers, _, _ = access(db, world)
    base.grant(db, world)
    db.execute(update(Visit).where(Visit.id == world['a2']).values(status=None, date_end=None))
    db.commit()
    first = client.get('/visitor/visits', params={'limit': 1}, headers=headers)
    private(first, 200)
    cursor = first.json()['next_cursor']
    assert cursor['as_of'] is None
    last = client.get('/visitor/visits', params={'after_start': cursor['date_start'], 'after_id': cursor['id']}, headers=headers)
    private(last, 200)
    row = last.json()['entries'][0]
    assert row['id'] == world['a2'] and row['status'] is None and row['end'] is None and row['price'] is None
    old = VisitorHistoryCursor(date_start=cursor['date_start'], id=cursor['id'])
    assert base.ids(views.list_visits(db, world['one'], cursor=old)) == [world['a2']]


def test_category_view_one_projection_real_readonly_and_equal_start_order(db, world, monkeypatch):
    base.grant(db, world)
    ids = [stored_visit(db, world, NOW, 'cancelled') for _ in range(5)]
    db.commit()
    db.execute(text('SET TRANSACTION READ ONLY'))
    statements = []
    def record(_conn, _cursor, statement, *_args):
        statements.append(statement)
    def forbidden(*_a, **_k):
        pytest.fail('Read committed or flushed')
    monkeypatch.setattr(db, 'flush', forbidden)
    monkeypatch.setattr(db, 'commit', forbidden)
    monkeypatch.setattr(views, 'utcnow', lambda: pytest.fail('Explicit boundary used wall clock'))
    event.listen(db.get_bind(), 'before_cursor_execute', record)
    try:
        first = views.list_visits(db, world['one'], category='cancelled', as_of=NOW, limit=2)
        second = views.list_visits(db, world['one'], category='cancelled', cursor=first.next_cursor, limit=2)
        third = views.list_visits(db, world['one'], category='cancelled', cursor=second.next_cursor, limit=2)
    finally:
        event.remove(db.get_bind(), 'before_cursor_execute', record)
    assert base.ids(first) + base.ids(second) + base.ids(third) == list(reversed(ids))
    assert len(statements) == 3 and all('authorized_history_visits' in s for s in statements)
    assert first.next_cursor.as_of == NOW and second.next_cursor.as_of == NOW


def test_http_offset_boundary_normalizes_before_filter(client, db, world):
    headers, _, _ = access(db, world)
    base.grant(db, world)
    db.commit()
    naive = client.get('/visitor/visits', params={'category': 'upcoming', 'as_of': NOW.isoformat()}, headers=headers)
    offset = (NOW + timedelta(hours=5, minutes=30)).replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))
    aware = client.get('/visitor/visits', params={'category': 'upcoming', 'as_of': offset.isoformat()}, headers=headers)
    private(naive, 200)
    private(aware, 200)
    assert naive.json() == aware.json()
    assert [r['id'] for r in aware.json()['entries']] == [world['a1']]
