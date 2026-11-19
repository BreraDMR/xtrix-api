"""CAL-09 review: the staff calendar reads a busy day (24 visits, two masters) through the
journal filters - date range, own-masters scope, timezone offsets - and never dumps the base."""
from datetime import datetime, timedelta

import pytest

from app.db.models.client import Client
from app.db.models.user import User
from app.db.models.visit import Visit

DAY = datetime(2030, 5, 6, 7, 0)  # Monday, 09:00 in Prague (summer time)


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def calendar(client, headers, **params):
    base = {'date_from': '2030-05-05T22:00:00Z', 'date_to': '2030-05-06T22:00:00Z',
            'calendar_overlap': True, 'per_page': 100, 'sort_by': 'date_start'}
    return client.get('/visits/', headers=headers, params={**base, **params})


def staff_member(client, db, headers, name, email):
    body = ok(client.post('/employees/create-with-user', json={
        'full_name': name, 'email': email, 'base_salary': 0,
        'permissions': {'can_create_visits': True, 'can_edit_clients': True, 'can_view_financials': True},
    }, headers=headers))
    return db.get(User, body['employee_user_id'])


@pytest.fixture
def busy_day(client, db, make_user, headers_for):
    owner = make_user('business_owner')
    oh = headers_for(owner)
    ok(client.post('/businesses/', json={'name': 'Calendar Salon', 'business_type': 'salon'}, headers=oh))
    anna = staff_member(client, db, oh, 'Anna Very-Long-Surname-For-The-Column', 'cal.anna@example.com')
    boris = staff_member(client, db, oh, 'Boris', 'cal.boris@example.com')
    person = ok(client.post('/clients/', json={'full_name': 'Calendar Client', 'gender': 'female'}, headers=oh))
    visits = {anna.id: [], boris.id: []}
    # 12 half-hour visits each, same slots for both masters - like a real Saturday rush
    for index in range(12):
        start = DAY + timedelta(minutes=30 * index)
        for master in (anna, boris):
            visits[master.id].append(ok(client.post('/visits/', json={
                'client_id': person['id'], 'specialist_id': master.id,
                'date_start': start.isoformat(), 'date_end': (start + timedelta(minutes=30)).isoformat(),
                'service_name': f'Cut {index:02d}', 'price': 500, 'status': 'planned',
            }, headers=oh)))
    # a late visit that starts the evening before and runs past midnight Prague time
    night = ok(client.post('/visits/', json={
        'client_id': person['id'], 'specialist_id': anna.id,
        'date_start': '2030-05-05T21:30:00Z', 'date_end': '2030-05-05T22:30:00Z',
        'service_name': 'Night colour', 'status': 'planned',
    }, headers=oh))
    # the next day must stay out of the grid
    ok(client.post('/visits/', json={
        'client_id': person['id'], 'specialist_id': boris.id,
        'date_start': '2030-05-07T08:00:00Z', 'date_end': '2030-05-07T09:00:00Z',
        'service_name': 'Tomorrow', 'status': 'planned',
    }, headers=oh))

    other = make_user('business_owner')
    xh = headers_for(other)
    ok(client.post('/businesses/', json={'name': 'Other Salon', 'business_type': 'salon'}, headers=xh))
    stranger = ok(client.post('/clients/', json={'full_name': 'Stranger', 'gender': 'male'}, headers=xh))
    ok(client.post('/visits/', json={
        'client_id': stranger['id'], 'specialist_id': other.id,
        'date_start': DAY.isoformat(), 'date_end': (DAY + timedelta(minutes=30)).isoformat(),
        'service_name': 'Foreign cut', 'status': 'planned',
    }, headers=xh))
    return dict(owner=owner, oh=oh, anna=anna, boris=boris, ah=headers_for(anna), bh=headers_for(boris),
                visits=visits, night=night, other=other)


def test_owner_sees_the_whole_day_for_own_masters_only(client, busy_day):
    body = ok(calendar(client, busy_day['oh']))
    names = [row['service_name'] for row in body['items']]
    assert body['total'] == 25
    assert 'Night colour' in names
    assert 'Tomorrow' not in names and 'Foreign cut' not in names
    masters = {row['specialist_id'] for row in body['items']}
    assert masters == {busy_day['anna'].id, busy_day['boris'].id}


def test_master_filter_narrows_the_grid_and_foreign_master_is_refused(client, busy_day):
    anna = busy_day['anna']
    body = ok(calendar(client, busy_day['oh'], specialist_id=anna.id))
    assert body['total'] == 13
    assert {row['specialist_id'] for row in body['items']} == {anna.id}
    assert calendar(client, busy_day['oh'], specialist_id=busy_day['other'].id).status_code == 403


def test_staff_member_cannot_widen_the_grid_to_a_colleague(client, busy_day):
    boris = busy_day['boris']
    for params in ({}, {'specialist_id': busy_day['anna'].id}, {'specialist_id': busy_day['owner'].id}):
        body = ok(calendar(client, busy_day['bh'], **params))
        assert body['total'] == 12
        assert {row['specialist_id'] for row in body['items']} == {boris.id}


def test_offsets_are_converted_not_dropped(client, busy_day):
    # Prague midnight written with +02:00 must equal 22:00Z, so the night visit is still there
    body = ok(calendar(client, busy_day['oh'], date_from='2030-05-06T00:00:00+02:00',
                       date_to='2030-05-07T00:00:00+02:00'))
    assert body['total'] == 25
    # dropping the offset would have started the day at 00:00Z and lost the night visit
    shifted = ok(calendar(client, busy_day['oh'], date_from='2030-05-06T00:00:00Z',
                          date_to='2030-05-07T00:00:00Z'))
    assert busy_day['night']['id'] not in [row['id'] for row in shifted['items']]


@pytest.mark.parametrize('params', [
    {'date_from': '2030-05-07T00:00:00Z', 'date_to': '2030-05-06T00:00:00Z', 'calendar_overlap': False},
    {'date_from': 'yesterday'},
    {'per_page': 101},
    {'page': 0},
])
def test_bad_ranges_and_oversized_pages_are_rejected(client, busy_day, params):
    assert calendar(client, busy_day['oh'], **params).status_code == 422


def test_solo_owner_without_business_sees_only_own_visits(client, db, make_user, headers_for, busy_day):
    # the API won't let a business-less owner add clients, so seed the rows directly
    solo = make_user('business_owner')
    person = Client(owner_id=solo.id, full_name='Solo Client')
    db.add(person)
    db.flush()
    mine = Visit(client_id=person.id, specialist_id=solo.id, date_start=DAY,
                 date_end=DAY + timedelta(hours=1), service_name='Solo cut', status='planned')
    db.add(mine)
    db.commit()
    body = ok(calendar(client, headers_for(solo)))
    assert [row['id'] for row in body['items']] == [mine.id]


def test_move_into_a_busy_slot_keeps_the_old_place(client, busy_day):
    first, second = busy_day['visits'][busy_day['anna'].id][:2]
    target = {'date_start': second['date_start'], 'date_end': second['date_end']}
    refused = client.put(f"/visits/{first['id']}", json=target, headers=busy_day['oh'])
    assert refused.status_code == 409, refused.text
    after = ok(calendar(client, busy_day['oh'], specialist_id=busy_day['anna'].id))
    row = next(item for item in after['items'] if item['id'] == first['id'])
    assert (row['date_start'], row['date_end']) == (first['date_start'], first['date_end'])
    # an empty afternoon slot is fine, and the length comes along
    free = {'date_start': '2030-05-06T14:00:00Z', 'date_end': '2030-05-06T14:30:00Z'}
    moved = ok(client.put(f"/visits/{first['id']}", json=free, headers=busy_day['oh']))
    assert (moved['date_start'], moved['date_end']) == (free['date_start'], free['date_end'])
