"""Category predicates partition exact granted history before pagination."""
from datetime import date, timedelta, timezone

import pytest
from sqlalchemy import event, select, text, update
from sqlalchemy.orm import Session

from app.api.core import visitor_history as history
from app.db.models.client import Client
from app.db.models.user import User
from app.db.models.visit import Visit
from tests import test_visitor_history as fixtures

world = fixtures.world
NOW = fixtures.NOW


def stored_visit(db, world, start, status, **values):
    # SQL preserves legacy NULL rather than applying the ORM insertion default.
    visit_id = fixtures.add_visit(db, world, start=start, status='cancelled', **values)
    db.execute(update(Visit).where(Visit.id == visit_id).values(status=status))
    return visit_id


def test_partition_preserves_null_unknown_and_completed_future(db, world):
    fixtures.grant(db, world)
    samples = [('cancelled', -5, 'cancelled'), ('cancelled', 5, 'cancelled'),
               ('completed', 6, 'past'), ('completed', -6, 'past'),
               (None, 0, 'upcoming'), ('', 2, 'upcoming'), ('custom', 3, 'upcoming'),
               ('planned', 4, 'upcoming'), ('in_progress', -2, 'past'),
               ('awaiting_payment', -3, 'past'), (None, -4, 'past')]
    expected = {'upcoming': {world['a1']}, 'past': {world['a2']}, 'cancelled': set()}
    raw = {}
    for status, hours, category in samples:
        visit_id = stored_visit(db, world, NOW + timedelta(hours=hours), status)
        expected[category].add(visit_id)
        raw[visit_id] = status
    db.commit()
    actual = {}
    for category in expected:
        page = history.list_visits(db, world['one'], category=category, as_of=NOW, limit=100)
        actual[category] = {entry.id for entry in page.entries}
        assert actual[category] == expected[category]
        for entry in page.entries:
            if entry.id in raw:
                assert entry.status == raw[entry.id]
    assert not (actual['upcoming'] & actual['past'] | actual['past'] & actual['cancelled']
                | actual['upcoming'] & actual['cancelled'])
    assert set.union(*actual.values()) == set(fixtures.ids(history.list_visits(db, world['one'], limit=100)))
    for visit_id, status in raw.items():
        assert history.get_visit(db, world['one'], visit_id).status == status


def test_query_builder_filters_before_limit_and_preserves_all_default(db, world):
    fixtures.grant(db, world)
    db.commit()
    query = history.visit_page_query(world['one'], category='past', as_of=NOW, limit=1)
    assert [row.id for row in db.execute(query)] == [world['a2']]
    assert history.list_visits(db, world['one']) == history.list_visits(
        db, world['one'], category='all', as_of=NOW + timedelta(days=10000))


@pytest.mark.parametrize('status', ['planned', None, '', 'unknown'])
def test_exact_start_boundary_and_aware_asof_are_utc(db, world, status):
    fixtures.grant(db, world)
    visit_id = stored_visit(db, world, NOW, status)
    db.commit()
    offset = (NOW + timedelta(hours=5, minutes=30)).replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))
    naive = history.list_visits(db, world['one'], category='upcoming', as_of=NOW)
    aware = history.list_visits(db, world['one'], category='upcoming', as_of=offset)
    assert naive == aware and visit_id in fixtures.ids(naive)
    assert visit_id not in fixtures.ids(history.list_visits(db, world['one'], category='past', as_of=offset))
    assert visit_id in fixtures.ids(history.list_visits(db, world['one'], category='past', as_of=NOW + timedelta(microseconds=1)))


def test_fresh_default_clock_only_for_non_all(db, world, monkeypatch):
    fixtures.grant(db, world)
    db.commit()
    ticks = []
    def now():
        ticks.append(True)
        return NOW
    monkeypatch.setattr(history, 'utcnow', now)
    assert fixtures.ids(history.list_visits(db, world['one'], category='upcoming')) == [world['a1']]
    assert len(ticks) == 1
    history.list_visits(db, world['one'])
    history.list_visits(db, world['one'], category='all', as_of=NOW)
    history.list_visits(db, world['one'], category='past', as_of=NOW)
    assert len(ticks) == 1
    monkeypatch.setattr(history, 'utcnow', lambda: pytest.fail('Explicit boundary used wall clock'))
    first = history.list_visits(db, world['one'], category='all', limit=1, as_of=NOW)
    last = history.list_visits(db, world['one'], category='all', limit=1, as_of=NOW, cursor=first.next_cursor)
    assert fixtures.ids(first) + fixtures.ids(last) == [world['a1'], world['a2']]
    assert not hasattr(first, 'as_of')


@pytest.mark.parametrize('category', [None, True, 0, [], {}, 'UPCOMING', '', 'future'])
def test_invalid_categories_fail_before_sql(db, category, monkeypatch):
    monkeypatch.setattr(db, 'execute', lambda *_a, **_k: pytest.fail('Invalid category reached SQL'))
    with pytest.raises(ValueError):
        history.list_visits(db, 1, category=category)
    with pytest.raises(ValueError):
        history.visit_page_query(1, category=category)


@pytest.mark.parametrize('as_of', [True, 0, '2026-10-26', date(2026, 10, 26), [], {}])
@pytest.mark.parametrize('category', ['all', 'past'])
def test_invalid_asof_fails_before_sql_even_for_all(db, as_of, category, monkeypatch):
    monkeypatch.setattr(db, 'execute', lambda *_a, **_k: pytest.fail('Invalid boundary reached SQL'))
    with pytest.raises(ValueError):
        history.list_visits(db, 1, category=category, as_of=as_of)
    with pytest.raises(ValueError):
        history.visit_page_query(1, category=category, as_of=as_of)


def test_more_than_200_mixed_rows_paginate_filtered_scope_without_gaps(db, world, monkeypatch):
    fixtures.grant(db, world)
    fixtures.grant(db, world, actor='two', business='b')
    fixtures.grant(db, world, visit=world['a1'])
    expected = {'upcoming': {world['a1']}, 'past': {world['a2']}, 'cancelled': set()}
    for n in range(225):
        status = ['cancelled', 'completed', 'planned', None, 'custom'][n % 5]
        start = NOW + timedelta(hours=(n - 112) * 2 + 1)
        visit_id = stored_visit(db, world, start, status)
        category = 'cancelled' if status == 'cancelled' else 'past' if status == 'completed' or start < NOW else 'upcoming'
        expected[category].add(visit_id)
    expected['cancelled'].update(stored_visit(db, world, NOW, 'cancelled') for _ in range(27))
    foreign = {stored_visit(db, world, NOW + timedelta(days=50 + n), 'cancelled', business='b') for n in range(3)}
    db.commit()
    monkeypatch.setattr(history, 'utcnow', lambda: pytest.fail('Fixed pagination boundary used wall clock'))
    for category, expected_ids in expected.items():
        cursor, received = None, []
        while True:
            page = history.list_visits(db, world['one'], category=category, as_of=NOW,
                                       business_id=world['a'], limit=7, cursor=cursor)
            received.extend(fixtures.ids(page))
            cursor = page.next_cursor
            if cursor is None:
                break
        ordered = list(db.scalars(select(Visit.id).where(Visit.id.in_(expected_ids)).order_by(Visit.date_start.desc(), Visit.id.desc())))
        assert received == ordered and len(set(received)) == len(received)
        assert not foreign.intersection(received)
        assert history.list_visits(db, world['one'], category=category, as_of=NOW, business_id=world['b']).entries == ()
    assert sum(map(len, expected.values())) == 254
    assert {entry.id for entry in history.list_visits(db, world['two'], category='cancelled', as_of=NOW).entries} == foreign
    db.execute(update(Visit).where(Visit.id == world['a1']).values(client_id=world['other_client']))
    db.commit()
    assert world['a1'] not in fixtures.ids(history.list_visits(db, world['one'], category='upcoming', as_of=NOW, limit=100))


def test_categories_are_real_readonly_without_autoflush(db, world, monkeypatch):
    fixtures.grant(db, world)
    db.commit()
    db.autoflush = True
    db.execute(text('SET TRANSACTION READ ONLY'))
    dirty = db.get(Client, world['client'])
    dirty.contact_phone = 'pending-private-edit'
    pending = User(full_name='Pending', email='pending-category@example.com', hashed_password='synthetic')
    db.add(pending)
    statements = []
    def record(_conn, _cursor, statement, *_args):
        statements.append(statement)
    def forbidden(*_args, **_kwargs):
        pytest.fail('Category read flushed or committed')
    monkeypatch.setattr(db, 'flush', forbidden)
    monkeypatch.setattr(db, 'commit', forbidden)
    event.listen(db.get_bind(), 'before_cursor_execute', record)
    try:
        assert fixtures.ids(history.list_visits(db, world['one'], category='upcoming', as_of=NOW)) == [world['a1']]
        assert fixtures.ids(history.list_visits(db, world['one'], category='past', as_of=NOW)) == [world['a2']]
        assert history.list_visits(db, world['one'], category='cancelled', as_of=NOW).entries == ()
    finally:
        event.remove(db.get_bind(), 'before_cursor_execute', record)
    assert len(statements) == 3 and all(s.lstrip().upper().startswith('SELECT') for s in statements)
    assert pending.id is None and pending in db.new and dirty in db.dirty
    with Session(db.get_bind()) as observer:
        assert observer.get(Client, world['client']).contact_phone == 'private-phone-canary'
        assert observer.query(User).filter_by(email=pending.email).count() == 0
