"""Visit responses respect the same financial checkbox as the client overview."""
import csv
import io
from datetime import timedelta

import pytest

from app.api.core.clock import utcnow
from app.db.models.user import User
from app.db.models.visit import Visit


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def salon(client, db, make_user, headers_for):
    owner = make_user('business_owner')
    owner_headers = headers_for(owner)
    business = ok(client.post('/businesses/', json={'name': 'Privacy Salon', 'business_type': 'salon'}, headers=owner_headers))
    employee = ok(client.post('/employees/create-with-user', json={
        'full_name': 'Privacy Staff', 'email': 'privacy.staff@example.com', 'base_salary': 0,
        'permissions': {'can_create_visits': True, 'can_view_financials': False},
    }, headers=owner_headers))
    staff = db.get(User, employee['employee_user_id'])
    customer = ok(client.post('/clients/', json={'full_name': 'Privacy Client'}, headers=owner_headers))
    services = [ok(client.post('/services/', json={
        'name': f'Privacy Service {index}', 'price': price, 'duration_minutes': 30,
        'specialist_id': staff.id,
    }, headers=owner_headers)) for index, price in enumerate((123.45, 67.89))]
    start = (utcnow() + timedelta(hours=3)).replace(minute=0, second=0, microsecond=0)
    body = {
        'client_id': customer['id'], 'specialist_id': staff.id,
        'date_start': start.isoformat(), 'service_id': services[0]['id'],
        'status': 'planned', 'payment_method': 'cash',
    }
    return dict(owner=owner, oh=owner_headers, eh=headers_for(staff), staff=staff,
                employee=employee, business=business, customer=customer, services=services, body=body)


def hidden(data):
    assert data['price'] is None
    assert data['payment_method'] is None
    assert data['items']
    assert all(item['price'] is None for item in data['items'])
    assert data['client_name'] == 'Privacy Client'
    assert data['service_name']
    assert data['date_start']


@pytest.mark.parametrize('chain', [False, True])
@pytest.mark.parametrize('endpoint', ['list', 'detail'])
def test_list_and_detail_hide_finances_without_mutating_storage(client, db, salon, chain, endpoint):
    body = dict(salon['body'])
    if chain:
        body['items'] = [{'service_id': service['id']} for service in salon['services']]
    original = ok(client.post('/visits/', json=body, headers=salon['oh']))
    expected_price = 191.34 if chain else 123.45
    assert original['price'] == pytest.approx(expected_price)
    if endpoint == 'list':
        listed = ok(client.get('/visits/', params={'client_id': salon['customer']['id']}, headers=salon['eh']))
        assert listed['total'] == 1
        hidden(listed['items'][0])
    else:
        hidden(ok(client.get(f"/visits/{original['id']}", headers=salon['eh'])))
    db.expire_all()
    stored = db.get(Visit, original['id'])
    assert float(stored.price) == pytest.approx(expected_price)
    assert stored.payment_method == 'cash'
    assert sum(float(item.price) for item in stored.items) == pytest.approx(expected_price)


@pytest.mark.parametrize('chain', [False, True])
def test_create_and_update_hide_response_and_keep_allowed_unsettled_write_policy(client, db, salon, chain):
    body = dict(salon['body'], status='completed')
    if chain:
        body['items'] = [{'service_id': service['id']} for service in salon['services']]
    created = ok(client.post('/visits/', json=body, headers=salon['eh']))
    hidden(created)
    updated = ok(client.put(f"/visits/{created['id']}", json={'note': 'Changed nonfinancial note'}, headers=salon['eh']))
    hidden(updated)
    owner_view = ok(client.get(f"/visits/{created['id']}", headers=salon['oh']))
    assert owner_view['price'] == pytest.approx(191.34 if chain else 123.45)
    assert sum(item['price'] for item in owner_view['items']) == pytest.approx(owner_view['price'])
    assert owner_view['payment_method'] == 'cash'
    assert owner_view['note'] == 'Changed nonfinancial note'
    # Reading permission does not override the settlement rule.
    if not chain:
        blocked = client.put(f"/visits/{created['id']}", json={'price': 98.76}, headers=salon['eh'])
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()['error']['detail']['code'] == 'visit_price_settlement_required'
        db.expire_all()
        assert float(db.get(Visit, created['id']).price) == 123.45
        hidden(ok(client.get(f"/visits/{created['id']}", headers=salon['eh'])))
        from datetime import datetime
        unpaid_body = dict(salon['body'], date_start=(datetime.fromisoformat(
            salon['body']['date_start']) + timedelta(days=2)).isoformat())
        unpaid = ok(client.post('/visits/', json=unpaid_body, headers=salon['eh']))
        hidden(ok(client.put(f"/visits/{unpaid['id']}", json={'price': 98.76}, headers=salon['eh'])))
        db.expire_all()
        assert float(db.get(Visit, unpaid['id']).price) == 98.76


def test_granted_staff_owner_and_admin_keep_finances(client, db, salon, make_user, headers_for):
    created = ok(client.post('/visits/', json=salon['body'], headers=salon['oh']))
    ok(client.put(f"/employees/{salon['employee']['id']}", json={
        'permissions': {'can_create_visits': True, 'can_view_financials': True},
    }, headers=salon['oh']))
    admin = make_user('admin')
    admin.business_id = salon['business']['id']
    db.commit()
    for headers in (salon['oh'], salon['eh'], headers_for(admin)):
        detail = ok(client.get(f"/visits/{created['id']}", headers=headers))
        assert detail['price'] == 123.45
        assert detail['payment_method'] == 'cash'
        assert detail['items'][0]['price'] == 123.45
        listed = ok(client.get('/visits/', params={'client_id': salon['customer']['id']}, headers=headers))
        assert listed['items'][0]['price'] == 123.45


@pytest.mark.parametrize('format', ['csv', 'excel'])
def test_exports_cannot_bypass_hidden_finances(client, salon, format):
    ok(client.post('/visits/', json=salon['body'], headers=salon['oh']))
    for allowed, expected in ((False, None), (True, 123.45)):
        ok(client.put(f"/employees/{salon['employee']['id']}", json={
            'permissions': {'can_view_financials': allowed},
        }, headers=salon['oh']))
        response = client.get(f'/visits/export/{format}', headers=salon['eh'])
        assert response.status_code == 200, response.text
        if format == 'csv':
            rows = list(csv.DictReader(io.StringIO(response.text.lstrip('\ufeff'))))
            assert len(rows) == 1
            assert rows[0]['Price'] == ('' if expected is None else str(expected))
        else:
            from openpyxl import load_workbook
            workbook = load_workbook(io.BytesIO(response.content), read_only=True)
            rows = list(workbook.active.values)
            assert len(rows) == 2
            assert rows[1][rows[0].index('Price')] == expected
            workbook.close()


def test_reminders_hide_response_but_sender_keeps_original_records(client, salon, monkeypatch):
    created = ok(client.post('/visits/', json=salon['body'], headers=salon['oh']))
    reminders = ok(client.get('/visits/reminders', headers=salon['eh']))
    assert [row['id'] for row in reminders] == [created['id']]
    hidden(reminders[0])
    received = []

    def local_sender(visits, channel, contacts):
        received.extend((visit.id, float(visit.price), visit.payment_method) for visit in visits)
        return len(visits)

    monkeypatch.setattr('app.api.v1.endpoints.visits.send_notifications', local_sender)
    sent = ok(client.post('/visits/reminders/send', params={'channel': 'log'}, headers=salon['eh']))
    assert sent['sent'] == 1
    assert received == [(created['id'], 123.45, 'cash')]
    assert ok(client.get(f"/visits/{created['id']}", headers=salon['oh']))['price'] == 123.45


def test_foreign_business_is_not_made_accessible_by_redaction(client, salon, make_user, headers_for):
    created = ok(client.post('/visits/', json=salon['body'], headers=salon['oh']))
    outsider = make_user('business_owner')
    headers = headers_for(outsider)
    ok(client.post('/businesses/', json={'name': 'Other Privacy Salon', 'business_type': 'salon'}, headers=headers))
    assert client.get(f"/visits/{created['id']}", headers=headers).status_code in (403, 404)
    assert client.get('/visits/', params={'client_id': salon['customer']['id']}, headers=headers).status_code == 403
