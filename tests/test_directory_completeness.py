"""DATA-01/DATA-04 regression: the client and visit lists stay complete past the first page.

Two salons built through the API, more than 100 clients and more than 20 visits in the bigger one.
Walking every page must give each allowed row exactly once (also when the sort key has ties),
total/pages must match, the wanted row past page one must be findable, and neither salon
may ever see the other's rows. A staff member limited to a few clients sees just those."""
from datetime import timedelta

import pytest

from app.api.core.clock import utcnow
from app.db.models.user import User


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def walk(client, path, headers, params=None, per_page=20):
    """Read every page and check total/pages on each one."""
    ids, page, first = [], 1, None
    while True:
        body = ok(client.get(path, headers=headers, params={**(params or {}), 'page': page, 'per_page': per_page}))
        first = first or (body['total'], body['pages'])
        assert (body['total'], body['pages']) == first
        assert body['page'] == page and body['per_page'] == per_page
        assert len(body['items']) == (min(per_page, body['total'] - (page - 1) * per_page) if body['total'] else 0)
        ids.extend(row['id'] for row in body['items'])
        if page >= body['pages']:
            break
        page += 1
    total, pages = first
    assert pages == (total + per_page - 1) // per_page
    assert len(ids) == total
    assert len(set(ids)) == len(ids), 'a row showed up on two pages'
    return ids


def build_salon(client, db, make_user, headers_for, name, clients_count, visits_count):
    owner = make_user('business_owner')
    oh = headers_for(owner)
    ok(client.post('/businesses/', json={'name': name, 'business_type': 'salon'}, headers=oh))
    employee = ok(client.post('/employees/create-with-user', json={
        'full_name': f'{name} Staff', 'email': f'{name.lower().replace(" ", ".")}.staff@example.com',
        'base_salary': 0,
        'permissions': {'can_create_visits': True, 'can_edit_clients': True, 'can_view_financials': True},
    }, headers=oh))
    staff = db.get(User, employee['employee_user_id'])
    clients = []
    for index in range(clients_count):
        # only two genders on purpose: sorting by gender gives long runs of ties
        body = {'full_name': f'{name} Person {index:03d}', 'gender': 'female' if index % 2 else 'male',
                'contact_email': f'{name.lower().replace(" ", "")}{index}@example.com'}
        clients.append(ok(client.post('/clients/', json=body, headers=oh)))
    start = (utcnow() + timedelta(days=3)).replace(hour=8, minute=0, second=0, microsecond=0)
    visits = []
    for index in range(visits_count):
        row = clients[-1 - index]  # the newest clients, so they sit past the first page by id
        visits.append(ok(client.post('/visits/', json={
            'client_id': row['id'], 'specialist_id': staff.id,
            'date_start': (start + timedelta(hours=index)).isoformat(),
            'date_end': (start + timedelta(hours=index, minutes=30)).isoformat(),
            'service_name': f'{name} Service {index:03d}', 'price': 100 if index % 2 else 200,
            'status': 'completed' if index % 3 == 0 else 'planned',
        }, headers=oh)))
    return dict(owner=owner, oh=oh, staff=staff, sh=headers_for(staff), employee_id=employee['id'],
                clients=clients, visits=visits)


@pytest.fixture
def salons(client, db, make_user, headers_for):
    big = build_salon(client, db, make_user, headers_for, 'Alpha', 105, 25)
    small = build_salon(client, db, make_user, headers_for, 'Beta', 23, 21)
    return big, small


CLIENT_SORTS = [{}, {'sort_by': 'gender'}, {'sort_by': 'gender', 'sort_order': 'desc'},
                {'sort_by': 'full_name', 'sort_order': 'desc'}, {'sort_by': 'created_at'}]
VISIT_SORTS = [{}, {'sort_by': 'status'}, {'sort_by': 'price', 'sort_order': 'desc'},
               {'sort_by': 'client_name'}, {'sort_by': 'date_start', 'sort_order': 'desc'}]


def test_every_page_walk_gives_each_salon_row_once(client, salons):
    for salon, other in (salons, salons[::-1]):
        own_clients = {row['id'] for row in salon['clients']}
        own_visits = {row['id'] for row in salon['visits']}
        for headers in (salon['oh'], salon['sh']):
            for params in CLIENT_SORTS:
                for per_page in (20, 100):
                    assert set(walk(client, '/clients/', headers, params, per_page)) == own_clients
            for params in VISIT_SORTS:
                for per_page in (7, 20):
                    assert set(walk(client, '/visits/', headers, params, per_page)) == own_visits
        # the other salon's rows never leak in, even through a search that matches them by text
        assert ok(client.get('/clients/', headers=salon['oh'], params={'q': other['clients'][0]['full_name']}))['total'] == 0
        assert ok(client.get('/visits/', headers=salon['oh'], params={'q': other['visits'][0]['service_name']}))['total'] == 0


def test_wanted_row_past_the_first_page_is_found_by_search_and_filters(client, salons):
    big, _ = salons
    target = big['clients'][104]
    first_page = ok(client.get('/clients/', headers=big['oh']))['items']
    assert target['id'] not in [row['id'] for row in first_page]
    for q in ('Alpha Person 104', 'alpha104@', 'PERSON 104'):
        body = ok(client.get('/clients/', headers=big['oh'], params={'q': q}))
        assert [row['id'] for row in body['items']] == [target['id']]
        assert (body['total'], body['pages']) == (1, 1)
    body = ok(client.get('/clients/', headers=big['oh'], params={'q': 'Alpha Person 10', 'gender': 'male'}))
    # 100, 102 and 104 - all past the first page; 101/103 are female
    assert [row['full_name'] for row in body['items']] == [f'Alpha Person {i}' for i in (100, 102, 104)]
    visit = big['visits'][24]
    body = ok(client.get('/visits/', headers=big['oh'], params={'q': 'Alpha Service 024'}))
    assert [row['id'] for row in body['items']] == [visit['id']]
    assert body['items'][0]['client_name'] == big['clients'][-25]['full_name']
    body = ok(client.get('/visits/', headers=big['oh'], params={'status': 'completed', 'per_page': 3, 'page': 3}))
    assert (body['total'], body['pages']) == (9, 3)
    assert [row['id'] for row in body['items']] == [big['visits'][i]['id'] for i in (18, 21, 24)]


def test_page_boundaries(client, salons):
    big, small = salons
    # 105 rows: last page holds 5, one past the end is empty but keeps the counts
    body = ok(client.get('/clients/', headers=big['oh'], params={'page': 6, 'per_page': 20}))
    assert (body['total'], body['pages'], len(body['items'])) == (105, 6, 5)
    body = ok(client.get('/clients/', headers=big['oh'], params={'page': 7, 'per_page': 20}))
    assert (body['total'], body['pages'], body['items']) == (105, 6, [])
    # exact fit: 21 visits by 7 is three full pages and no fourth
    assert ok(client.get('/visits/', headers=small['oh'], params={'per_page': 7}))['pages'] == 3
    assert ok(client.get('/visits/', headers=small['oh'], params={'per_page': 7, 'page': 4}))['items'] == []
    # a search with no hits says 0 pages, not 1
    body = ok(client.get('/clients/', headers=big['oh'], params={'q': 'nobody here'}))
    assert (body['total'], body['pages'], body['items']) == (0, 0, [])
    for path in ('/clients/', '/visits/'):
        for bad in ({'per_page': 101}, {'per_page': 0}, {'page': 0}, {'page': 100_001}):
            assert client.get(path, headers=big['oh'], params=bad).status_code == 422, (path, bad)


def test_staff_limited_to_assigned_clients_sees_just_those_past_page_one(client, salons):
    big, small = salons
    picked = [big['clients'][i]['id'] for i in (3, 64, 104)]
    # a foreign id in the saved list must not open anything
    ok(client.put(f"/employees/{big['employee_id']}", headers=big['oh'], json={'permissions': {
        'can_create_visits': True, 'can_edit_clients': True, 'can_view_financials': True,
        'assigned_client_ids': picked + [small['clients'][0]['id']],
    }}))
    ids = walk(client, '/clients/', big['sh'], per_page=2)
    assert sorted(ids) == sorted(picked)
    # owner still sees the whole salon
    assert len(walk(client, '/clients/', big['oh'], per_page=100)) == 105
