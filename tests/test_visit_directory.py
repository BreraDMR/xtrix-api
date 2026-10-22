"""Visit filters and ordering operate on the whole allowed history."""
from datetime import datetime, timedelta

from app.db.models.client import Client
from app.db.models.visit import Visit


def history(db, make_user):
    owner = make_user('specialist')
    other = make_user('specialist')
    clients = [Client(owner_id=owner.id, full_name=f'Person {i:03d}') for i in range(125)]
    db.add_all(clients)
    db.flush()
    start = datetime(2026, 10, 1, 9)
    visits = [Visit(client_id=row.id, specialist_id=owner.id,
                    date_start=start + timedelta(days=i), service_name=f'Service {i:03d}',
                    status='completed' if i % 2 else 'planned', price=i)
              for i, row in enumerate(clients)]
    db.add_all(visits + [Visit(client_id=clients[124].id, specialist_id=other.id,
                              date_start=start, service_name='Foreign secret', status='completed')])
    db.commit()
    return owner, visits


def test_search_and_status_include_later_pages_without_foreign_visits(client, db, make_user, headers_for):
    owner, visits = history(db, make_user)
    headers = headers_for(owner)
    body = client.get('/visits/', headers=headers, params={'q': 'Person 124'}).json()
    assert body['total'] == 1
    assert body['items'][0]['id'] == visits[124].id
    assert body['items'][0]['client_name'] == 'Person 124'
    body = client.get('/visits/', headers=headers, params={
        'status': 'completed', 'sort_by': 'price', 'sort_order': 'desc', 'page': 2, 'per_page': 10,
    }).json()
    assert (body['total'], body['pages']) == (62, 7)
    assert [row['price'] for row in body['items']] == list(range(103, 84, -2))


def test_visit_pagination_is_stable_and_filters_before_counting(client, db, make_user, headers_for):
    owner, visits = history(db, make_user)
    headers = headers_for(owner)
    seen = []
    for page in range(1, 8):
        body = client.get('/visits/', headers=headers, params={'page': page}).json()
        assert (body['total'], body['pages']) == (125, 7)
        seen.extend(row['id'] for row in body['items'])
    assert seen == [row.id for row in visits]
    body = client.get('/visits/', headers=headers, params={
        'q': 'Service', 'date_from': '2026-10-02T00:00:00Z', 'date_to': '2026-10-03T23:59:59Z',
    }).json()
    assert [row['id'] for row in body['items']] == [visits[1].id, visits[2].id]
    assert client.get('/visits/', headers=headers, params={'q': '%_'}).json()['total'] == 0
    assert client.get('/visits/', headers=headers, params={'sort_by': 'hashed_password'}).status_code == 422
