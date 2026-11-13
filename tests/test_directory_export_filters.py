"""CSV/Excel exports give back exactly the rows the list shows for the same filters,
across the whole allowed base (not just the first page), and never other salons' rows."""
import csv
import io
from datetime import timedelta

import pytest
from openpyxl import load_workbook

from app.api.core.clock import utcnow
from app.db.models.user import User


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def csv_ids(response):
    assert response.status_code == 200, response.text
    return [int(row['ID']) for row in csv.DictReader(io.StringIO(response.text))]


def excel_ids(response):
    assert response.status_code == 200, response.text
    sheet = load_workbook(io.BytesIO(response.content)).active
    rows = list(sheet.iter_rows(values_only=True))
    position = list(rows[0]).index('ID')
    return [int(row[position]) for row in rows[1:]]


def list_ids(client, path, headers, params):
    ids, page = [], 1
    while True:
        body = ok(client.get(path, headers=headers, params={**params, 'page': page, 'per_page': 100}))
        ids.extend(row['id'] for row in body['items'])
        if page >= body['pages']:
            return ids
        page += 1


@pytest.fixture
def salon(client, db, make_user, headers_for):
    owner = make_user('business_owner')
    oh = headers_for(owner)
    ok(client.post('/businesses/', json={'name': 'Export Salon', 'business_type': 'salon'}, headers=oh))
    employee = ok(client.post('/employees/create-with-user', json={
        'full_name': 'Export Staff', 'email': 'export.staff@example.com', 'base_salary': 0,
        'permissions': {'can_create_visits': True, 'can_edit_clients': True, 'can_view_financials': True},
    }, headers=oh))
    staff = db.get(User, employee['employee_user_id'])
    eh = headers_for(staff)
    clients = []
    # more than one page of clients, a mix of owner-made and staff-made cards
    for index in range(130):
        body = {'full_name': f'Export Person {index:03d}', 'gender': 'female' if index % 2 else 'male',
                'contact_email': f'export{index}@example.com'}
        clients.append(ok(client.post('/clients/', json=body, headers=eh if index % 5 == 0 else oh)))
    start = (utcnow() + timedelta(days=2)).replace(hour=8, minute=0, second=0, microsecond=0)
    visits = []
    for index, row in enumerate(clients[:45]):
        visits.append(ok(client.post('/visits/', json={
            'client_id': row['id'], 'specialist_id': staff.id,
            'date_start': (start + timedelta(hours=index)).isoformat(),
            'date_end': (start + timedelta(hours=index, minutes=30)).isoformat(),
            'service_name': f'Export Service {index:03d}', 'price': 100 + index,
            'status': 'completed' if index % 3 == 0 else 'planned',
        }, headers=oh)))

    other = make_user('business_owner')
    xh = headers_for(other)
    ok(client.post('/businesses/', json={'name': 'Foreign Salon', 'business_type': 'salon'}, headers=xh))
    foreign = ok(client.post('/clients/', json={'full_name': 'Export Person foreign', 'gender': 'female',
                                                'contact_email': 'export1@example.com'}, headers=xh))
    foreign_visit = ok(client.post('/visits/', json={
        'client_id': foreign['id'], 'specialist_id': other.id,
        'date_start': start.isoformat(), 'date_end': (start + timedelta(minutes=30)).isoformat(),
        'service_name': 'Export Service foreign', 'status': 'completed',
    }, headers=xh))
    return dict(owner=owner, oh=oh, staff=staff, eh=eh, other=other, xh=xh, clients=clients,
                visits=visits, foreign=foreign, foreign_visit=foreign_visit)


CLIENT_FILTERS = [
    {},
    {'q': 'export12'},
    {'q': 'Person 07'},
    {'gender': 'female', 'sort_by': 'full_name', 'sort_order': 'desc'},
    {'q': 'Person 1', 'gender': 'male', 'sort_by': 'contact_email'},
]


@pytest.mark.parametrize('params', CLIENT_FILTERS)
def test_client_export_matches_the_filtered_list(client, salon, params):
    for headers in (salon['oh'], salon['eh']):
        expected = list_ids(client, '/clients/', headers, params)
        assert csv_ids(client.get('/clients/export/csv', headers=headers, params=params)) == expected
        assert excel_ids(client.get('/clients/export/excel', headers=headers, params=params)) == expected
    assert salon['foreign']['id'] not in expected


def test_owner_client_export_covers_staff_made_cards_beyond_the_first_page(client, salon):
    exported = csv_ids(client.get('/clients/export/csv', headers=salon['oh']))
    assert sorted(exported) == sorted(row['id'] for row in salon['clients'])
    assert salon['foreign']['id'] not in exported


VISIT_FILTERS = [
    {},
    {'q': 'Export Person 03'},
    {'status': 'completed'},
    {'status': 'planned', 'sort_by': 'price', 'sort_order': 'desc'},
    {'q': 'Service 01', 'sort_by': 'client_name'},
]


@pytest.mark.parametrize('params', VISIT_FILTERS)
def test_visit_export_matches_the_filtered_list(client, salon, params):
    for headers in (salon['oh'], salon['eh']):
        expected = list_ids(client, '/visits/', headers, params)
        assert csv_ids(client.get('/visits/export/csv', headers=headers, params=params)) == expected
        assert excel_ids(client.get('/visits/export/excel', headers=headers, params=params)) == expected
    assert salon['foreign_visit']['id'] not in expected


def test_owner_visit_export_covers_staff_visits_and_specialist_filter(client, salon):
    exported = csv_ids(client.get('/visits/export/csv', headers=salon['oh']))
    assert sorted(exported) == sorted(row['id'] for row in salon['visits'])
    by_staff = csv_ids(client.get('/visits/export/csv', headers=salon['oh'],
                                  params={'specialist_id': salon['staff'].id}))
    assert sorted(by_staff) == sorted(exported)


def test_exports_refuse_foreign_scope_like_the_list(client, salon):
    foreign_specialist = {'specialist_id': salon['other'].id}
    assert client.get('/visits/', headers=salon['oh'], params=foreign_specialist).status_code == 403
    assert client.get('/visits/export/csv', headers=salon['oh'], params=foreign_specialist).status_code == 403
    foreign_client = {'client_id': salon['foreign']['id']}
    assert client.get('/visits/', headers=salon['oh'], params=foreign_client).status_code == 403
    assert client.get('/visits/export/excel', headers=salon['oh'], params=foreign_client).status_code == 403
    # the foreign owner sees only their own rows
    assert csv_ids(client.get('/clients/export/csv', headers=salon['xh'])) == [salon['foreign']['id']]
    assert csv_ids(client.get('/visits/export/csv', headers=salon['xh'])) == [salon['foreign_visit']['id']]


def test_export_rejects_bad_filters_like_the_list(client, salon):
    assert client.get('/clients/export/csv', headers=salon['oh'], params={'sort_by': 'password'}).status_code == 422
    assert client.get('/visits/export/csv', headers=salon['oh'], params={'status': 'lost'}).status_code == 422
