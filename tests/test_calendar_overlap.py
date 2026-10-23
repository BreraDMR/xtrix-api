"""Calendar ranges include overnight visits without changing history filters."""
from datetime import datetime

import pytest

from app.db.models.client import Client
from app.db.models.visit import Visit


def test_calendar_range_uses_half_open_overlap_before_pagination(client, db, make_user, headers_for):
    owner = make_user('specialist')
    other = make_user('specialist')
    person = Client(owner_id=owner.id, full_name='Night client')
    db.add(person)
    db.flush()
    rows = []
    for specialist, start, end in [
        (owner, '2026-10-24T21:30', '2026-10-24T23:00'),
        (owner, '2026-10-24T20:00', '2026-10-24T22:00'),
        (owner, '2026-10-25T23:00', '2026-10-26T00:00'),
        (owner, '2026-10-25T00:45', '2026-10-25T01:15'),
        (other, '2026-10-25T12:00', '2026-10-25T13:00'),
    ]:
        row = Visit(client_id=person.id, specialist_id=specialist.id,
                    date_start=datetime.fromisoformat(start), date_end=datetime.fromisoformat(end),
                    service_name='Night service', status='planned')
        rows.append(row)
    # A cancelled boundary visit stays visible, but does not occupy the overnight slot.
    rows[1].status = 'cancelled'
    db.add_all(rows)
    db.commit()
    # Prague's autumn transition day lasts 25 hours.
    params = {'date_from': '2026-10-24T22:00:00Z', 'date_to': '2026-10-25T23:00:00Z',
              'calendar_overlap': True, 'per_page': 1, 'sort_by': 'date_start'}
    headers = headers_for(owner)
    first = client.get('/visits/', params=params, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()['total'] == 2
    assert [row['id'] for row in first.json()['items']] == [rows[0].id]
    second = client.get('/visits/', params={**params, 'page': 2}, headers=headers)
    assert [row['id'] for row in second.json()['items']] == [rows[3].id]
    legacy = client.get('/visits/', params={**params, 'calendar_overlap': False, 'per_page': 100}, headers=headers)
    assert [row['id'] for row in legacy.json()['items']] == [rows[3].id, rows[2].id]


@pytest.mark.parametrize('params', [
    {'calendar_overlap': True},
    {'calendar_overlap': True, 'date_from': '2026-10-25T00:00:00Z'},
    {'calendar_overlap': True, 'date_to': '2026-10-26T00:00:00Z'},
    {'calendar_overlap': True, 'date_from': '2026-10-25T00:00:00Z', 'date_to': '2026-10-25T00:00:00Z'},
])
def test_calendar_overlap_requires_nonempty_bounded_range(client, make_user, headers_for, params):
    assert client.get('/visits/', params=params, headers=headers_for(make_user('specialist'))).status_code == 422


def test_calendar_missing_end_uses_the_grid_hour_fallback(client, db, make_user, headers_for):
    owner = make_user('specialist')
    person = Client(owner_id=owner.id, full_name='Late client')
    db.add(person)
    db.flush()
    row = Visit(client_id=person.id, specialist_id=owner.id,
                date_start=datetime(2026, 11, 1, 23, 30), service_name='Late service', status='planned')
    db.add(row)
    db.commit()
    response = client.get('/visits/', headers=headers_for(owner), params={
        'calendar_overlap': True, 'date_from': '2026-11-02T00:00:00Z', 'date_to': '2026-11-03T00:00:00Z',
    })
    assert response.status_code == 200
    assert [item['id'] for item in response.json()['items']] == [row.id]
