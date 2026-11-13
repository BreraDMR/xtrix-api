"""Walk every GET route as outsiders and look for one salon's marker data in the answers.

Salon A gets a client, a priced service and a completed visit with odd, easy-to-spot values.
Then another owner, a solo specialist and A's own staff without financial rights call every
GET path with A's ids stuffed into path and query params. Nobody outside A may see A's
client or salon, and the staff member may not see A's money.
"""
import re
from datetime import timedelta

from app.api.core.clock import utcnow
from app.db.models.user import User
from tests.test_visit_financial_privacy import ok

PRICE = '98765.43'
CLIENT_NAME = 'Canaryquxzel Wrobel'
CLIENT_PHONE = '+420777123987'
SALON_NAME = 'Canary Salon Zq'
SERVICE_NAME = 'Canary Service Zq'

# Staff without financial rights still sees the catalog price of a service they work on -
# they quote it to the client. Everything else with money stays hidden from them.
STAFF_PRICE_ALLOWED = {'/services/{service_id}'}


def _get_paths(app):
    # app.routes only has lazy wrappers here, the OpenAPI schema is the full list
    return sorted(path for path, ops in app.openapi()['paths'].items() if 'get' in ops)


def test_outsiders_never_see_another_salons_data(client, db, make_user, headers_for):
    from app.main import app

    owner = make_user('business_owner')
    owner_headers = headers_for(owner)
    salon = ok(client.post('/businesses/', json={'name': SALON_NAME, 'business_type': 'salon'},
                           headers=owner_headers))
    employee = ok(client.post('/employees/create-with-user', json={
        'full_name': 'Canary Staff', 'email': 'canary.staff@example.com', 'base_salary': 0,
        'permissions': {'can_create_visits': True, 'can_view_financials': False},
    }, headers=owner_headers))
    staff = db.get(User, employee['employee_user_id'])
    customer = ok(client.post('/clients/', json={'full_name': CLIENT_NAME, 'phone': CLIENT_PHONE},
                              headers=owner_headers))
    service = ok(client.post('/services/', json={
        'name': SERVICE_NAME, 'price': float(PRICE), 'duration_minutes': 30, 'specialist_id': staff.id,
    }, headers=owner_headers))
    start = (utcnow() - timedelta(days=2)).replace(minute=0, second=0, microsecond=0)
    visit = ok(client.post('/visits/', json={
        'client_id': customer['id'], 'specialist_id': staff.id, 'date_start': start.isoformat(),
        'service_id': service['id'], 'status': 'completed', 'payment_method': 'cash',
    }, headers=owner_headers))

    other_owner = make_user('business_owner')
    other_headers = headers_for(other_owner)
    ok(client.post('/businesses/', json={'name': 'Other Salon', 'business_type': 'salon'}, headers=other_headers))
    callers = {
        'other_owner': other_headers,
        'solo_specialist': headers_for(make_user('specialist')),
        'staff_without_financials': headers_for(staff),
        'owner_control': owner_headers,
    }
    ids = {'visit_id': visit['id'], 'client_id': customer['id'], 'service_id': service['id'],
           'business_id': salon['id'], 'employee_id': employee['id'], 'user_id': owner.id,
           'specialist_id': staff.id, 'owner_id': owner.id, 'id': visit['id']}
    query = {'start': '2020-01-01T00:00:00Z', 'end': '2030-01-01T00:00:00Z',
             'date_from': '2020-01-01T00:00:00Z', 'date_to': '2030-01-01T00:00:00Z',
             'business_id': salon['id'], 'specialist_id': staff.id, 'client_id': customer['id'],
             'owner_id': owner.id}

    leaks, answered, control_hits = [], 0, 0
    for template in _get_paths(app):
        path = re.sub(r'\{(\w+)(:[^}]*)?\}', lambda m: str(ids.get(m.group(1), 1)), template)
        for who, headers in callers.items():
            response = client.get(path, params=query, headers=headers)
            if response.status_code >= 400:
                continue
            body = response.text
            has_money = PRICE in body or '98765' in body
            if who == 'owner_control':
                answered += 1
                control_hits += has_money or CLIENT_NAME in body
                continue
            found = []
            if who != 'staff_without_financials':
                found = [m for m in (CLIENT_NAME, CLIENT_PHONE, SALON_NAME, SERVICE_NAME) if m in body]
            if has_money and not (who == 'staff_without_financials' and template in STAFF_PRICE_ALLOWED):
                found.append('price')
            if found:
                leaks.append(f'{who} GET {template} -> {response.status_code} {found}')

    # the sweep is only worth something if the owner really gets data back from many routes
    assert answered > 50 and control_hits > 10, (answered, control_hits)
    assert not leaks, '\n'.join(leaks)
