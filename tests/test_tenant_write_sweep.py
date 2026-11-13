"""Outsiders hit every body-less write route with another salon's ids: nothing may succeed or change.

Covers actions like delete, restore, unarchive, verify, cancel and send. Routes that need a
request body would mostly stop at validation here, so they are left to their own tests.
"""
import re
from datetime import timedelta

from sqlalchemy import text

from app.api.core.clock import utcnow
from app.db.models.user import User
from tests.test_visit_financial_privacy import ok

TABLES = ('visits', 'visit_items', 'clients', 'services', 'businesses', 'employees', 'users',
          'client_notes', 'waitlist_entries', 'products', 'expenses')


def _digest(db):
    # one hash per table over every column of every row - any write shows up
    result = {}
    for table in TABLES:
        exists = db.execute(text('SELECT to_regclass(:name)'), {'name': table}).scalar()
        if exists:
            result[table] = db.execute(text(
                f"SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), '')) FROM {table} t")).scalar()
    db.rollback()
    return result


def test_outsiders_cannot_run_bodyless_writes_on_another_salon(client, db, make_user, headers_for):
    from app.main import app

    owner = make_user('business_owner')
    owner_headers = headers_for(owner)
    salon = ok(client.post('/businesses/', json={'name': 'Write Canary Salon', 'business_type': 'salon'},
                           headers=owner_headers))
    employee = ok(client.post('/employees/create-with-user', json={
        'full_name': 'Write Canary Staff', 'email': 'write.canary.staff@example.com', 'base_salary': 0,
        'permissions': {'can_create_visits': True, 'can_view_financials': False},
    }, headers=owner_headers))
    staff = db.get(User, employee['employee_user_id'])
    customer = ok(client.post('/clients/', json={'full_name': 'Write Canary Client'}, headers=owner_headers))
    service = ok(client.post('/services/', json={
        'name': 'Write Canary Service', 'price': 50.0, 'duration_minutes': 30, 'specialist_id': staff.id,
    }, headers=owner_headers))
    start = (utcnow() + timedelta(days=2)).replace(minute=0, second=0, microsecond=0)
    visit = ok(client.post('/visits/', json={
        'client_id': customer['id'], 'specialist_id': staff.id, 'date_start': start.isoformat(),
        'service_id': service['id'], 'status': 'planned',
    }, headers=owner_headers))

    other_headers = headers_for(make_user('business_owner'))
    ok(client.post('/businesses/', json={'name': 'Other Salon', 'business_type': 'salon'}, headers=other_headers))
    callers = {'other_owner': other_headers, 'solo_specialist': headers_for(make_user('specialist'))}
    ids = {'visit_id': visit['id'], 'client_id': customer['id'], 'service_id': service['id'],
           'business_id': salon['id'], 'employee_id': employee['id'], 'user_id': owner.id,
           'specialist_id': staff.id, 'id': visit['id']}

    before = _digest(db)
    allowed, refused = [], set()
    for path, operations in sorted(app.openapi()['paths'].items()):
        if '{' not in path:
            continue
        for method in ('post', 'put', 'patch', 'delete'):
            operation = operations.get(method)
            if not operation or (operation.get('requestBody') or {}).get('required'):
                continue
            url = re.sub(r'\{(\w+)(:[^}]*)?\}', lambda m: str(ids.get(m.group(1), 1)), path)
            for who, headers in callers.items():
                response = client.request(method.upper(), url, headers=headers, params={'business_id': salon['id']})
                if response.status_code < 400:
                    allowed.append(f'{who} {method.upper()} {path} -> {response.status_code}')
                elif response.status_code == 403:
                    refused.add(f'{method.upper()} {path}')

    assert not allowed, '\n'.join(allowed)
    assert _digest(db) == before
    # the sweep must really reach the handlers, not stop at routing or validation
    assert len(refused) >= 20, sorted(refused)
