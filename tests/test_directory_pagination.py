"""Directory filters apply before pagination and keep the caller's tenant scope."""
from datetime import datetime

from app.db.models.client import Client


def directory(db, make_user):
    owner = make_user('specialist')
    other = make_user('specialist')
    rows = [Client(owner_id=owner.id, full_name=f'Client {index:03d}',
                   contact_email=f'person{index}@example.com', gender='female' if index % 2 else 'male')
            for index in range(125)]
    db.add_all(rows + [
        Client(owner_id=other.id, full_name='Other salon secret', contact_email='person124@example.com'),
        Client(owner_id=owner.id, full_name='Archived', archived_at=datetime(2026, 1, 1)),
        Client(owner_id=owner.id, full_name='Deleted', deleted_at=datetime(2026, 1, 1)),
    ])
    db.commit()
    return owner, rows


def test_client_pages_cover_all_records_with_stable_order(client, db, make_user, headers_for):
    owner, rows = directory(db, make_user)
    headers = headers_for(owner)
    seen = []
    for page in range(1, 8):
        response = client.get('/clients/', headers=headers, params={'page': page, 'per_page': 20})
        assert response.status_code == 200
        body = response.json()
        assert (body['total'], body['pages']) == (125, 7)
        seen.extend(row['id'] for row in body['items'])
    assert seen == [row.id for row in rows]


def test_client_search_gender_and_sort_cover_rows_beyond_first_page(client, db, make_user, headers_for):
    owner, rows = directory(db, make_user)
    headers = headers_for(owner)
    body = client.get('/clients/', headers=headers, params={'q': 'person124@'}).json()
    assert body['total'] == 1
    assert [row['id'] for row in body['items']] == [rows[124].id]
    body = client.get('/clients/', headers=headers, params={
        'gender': 'female', 'sort_by': 'full_name', 'sort_order': 'desc', 'per_page': 10, 'page': 2,
    }).json()
    assert (body['total'], body['pages']) == (62, 7)
    assert [row['full_name'] for row in body['items']] == [f'Client {index:03d}' for index in range(103, 84, -2)]
    body = client.get('/clients/', headers=headers, params={
        'q': 'person124@', 'include_deleted': True, 'include_archived': True,
    }).json()
    assert [row['id'] for row in body['items']] == [rows[124].id]


def test_directory_search_treats_wildcards_as_text_and_validates_sort(client, db, make_user, headers_for):
    owner, _ = directory(db, make_user)
    headers = headers_for(owner)
    assert client.get('/clients/', headers=headers, params={'q': '%_'}).json()['total'] == 0
    assert client.get('/clients/', headers=headers, params={'sort_by': 'password'}).status_code == 422
