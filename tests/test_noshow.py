"""No-shows and late cancels: who may mark them, when, and what they must not touch."""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.api.core import attendance
from app.api.core.clock import utcnow
from app.db.models.business import Business
from app.db.models.business_audit_event import BusinessAuditEvent
from app.db.models.employee import Employee
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.user import User
from app.db.models.visit import Visit


def ok(response):
    assert response.status_code in (200, 201), response.text
    return response.json()


@pytest.fixture
def salon(client, db, make_user, headers_for):
    owner = make_user('business_owner')
    oh = headers_for(owner)
    business = ok(client.post('/businesses/', json={'name': 'Attendance Salon', 'business_type': 'salon'}, headers=oh))
    employee = ok(client.post('/employees/create-with-user', json={
        'full_name': 'Attendance Staff', 'email': 'attendance.staff@example.com', 'base_salary': 0,
        'permissions': {'can_create_visits': True, 'can_view_financials': False},
    }, headers=oh))
    staff = db.get(User, employee['employee_user_id'])
    customer = ok(client.post('/clients/', json={'full_name': 'Attendance Client',
                                                 'contact_phone': '+420777000111'}, headers=oh))
    service = ok(client.post('/services/', json={
        'name': 'Attendance Cut', 'price': 100, 'duration_minutes': 30, 'specialist_id': staff.id,
    }, headers=oh))
    world = dict(owner=owner, oh=oh, eh=headers_for(staff), staff=staff, employee=employee,
                 business=business, customer=customer, service=service)

    def visit(hours_from_now, **extra):
        start = (utcnow() + timedelta(hours=hours_from_now)).replace(second=0, microsecond=0)
        body = {'client_id': customer['id'], 'specialist_id': staff.id, 'service_id': service['id'],
                'date_start': start.isoformat(), 'status': 'planned', **extra}
        return ok(client.post('/visits/', json=body, headers=oh))

    world['visit'] = visit
    return world


def _actions(db, business_id):
    db.expire_all()
    return [row.action for row in db.query(BusinessAuditEvent).filter(
        BusinessAuditEvent.business_id == business_id).order_by(BusinessAuditEvent.id)]


def test_started_visit_can_be_marked_and_unmarked_and_both_are_journaled(client, db, salon):
    visit = salon['visit'](-2)
    marked = ok(client.put(f"/visits/{visit['id']}", json={'status': 'no_show'}, headers=salon['eh']))
    assert marked['status'] == 'no_show'
    listed = ok(client.get('/visits/', params={'status': 'no_show'}, headers=salon['oh']))
    assert [row['id'] for row in listed['items']] == [visit['id']]

    undone = ok(client.put(f"/visits/{visit['id']}", json={'status': 'planned'}, headers=salon['oh']))
    assert undone['status'] == 'planned'
    actions = _actions(db, salon['business']['id'])
    assert actions[-2:] == ['visit.no_show', 'visit.no_show_reverted']
    row = db.query(BusinessAuditEvent).filter(BusinessAuditEvent.action == 'visit.no_show').one()
    assert row.changes['status'] == {'old': 'planned', 'new': 'no_show'}
    assert row.actor_user_id == salon['staff'].id


def test_future_or_finished_visit_cannot_be_a_no_show(client, db, salon):
    future = salon['visit'](5)
    response = client.put(f"/visits/{future['id']}", json={'status': 'no_show'},
                          headers={**salon['oh'], 'Accept-Language': 'cz'})
    assert response.status_code == 422, response.text
    assert response.json()['error']['detail']['code'] == 'visit_no_show_not_started'
    assert 'Nedostavení' in response.json()['error']['message']

    done = salon['visit'](-3, status='completed', payment_method='cash')
    response = client.put(f"/visits/{done['id']}", json={'status': 'no_show'}, headers=salon['oh'])
    assert response.status_code == 422, response.text
    assert response.json()['error']['detail']['code'] == 'visit_no_show_status'
    db.expire_all()
    assert db.get(Visit, future['id']).status == 'planned'
    assert db.get(Visit, done['id']).status == 'completed'


def test_a_visit_cannot_be_created_as_a_no_show(client, salon):
    start = (utcnow() - timedelta(hours=2)).isoformat()
    response = client.post('/visits/', json={
        'client_id': salon['customer']['id'], 'specialist_id': salon['staff'].id,
        'service_id': salon['service']['id'], 'date_start': start, 'status': 'no_show',
    }, headers=salon['oh'])
    assert response.status_code == 422, response.text


def test_staff_without_booking_rights_cannot_mark_or_undo(client, db, salon):
    visit = salon['visit'](-1)
    employee = db.get(Employee, salon['employee']['id'])
    employee.permissions = {**(employee.permissions or {}), 'can_create_visits': False}
    db.commit()
    response = client.put(f"/visits/{visit['id']}", json={'status': 'no_show'}, headers=salon['eh'])
    assert response.status_code == 403, response.text
    ok(client.put(f"/visits/{visit['id']}", json={'status': 'no_show'}, headers=salon['oh']))
    response = client.put(f"/visits/{visit['id']}", json={'status': 'planned'}, headers=salon['eh'])
    assert response.status_code == 403, response.text
    db.expire_all()
    assert db.get(Visit, visit['id']).status == 'no_show'


def test_marking_a_no_show_sends_nothing_to_the_client(client, db, salon):
    visit = salon['visit'](-1)
    db.expire_all()
    before = db.query(NotificationOutbox).count()
    ok(client.put(f"/visits/{visit['id']}", json={'status': 'no_show'}, headers=salon['oh']))
    ok(client.post('/visits/reminders/send', headers=salon['oh']))
    db.expire_all()
    assert db.query(NotificationOutbox).count() == before
    from app.api.core.notification_scheduler import sync_reminders
    assert sync_reminders(db)['scheduled'] == 0


def test_cancel_inside_the_window_is_late_and_staff_can_waive_it(client, db, salon):
    soon = salon['visit'](3)
    later = salon['visit'](24 * 3)
    late = ok(client.put(f"/visits/{soon['id']}", json={'status': 'cancelled'}, headers=salon['oh']))
    assert late['late_cancel'] is True
    early = ok(client.put(f"/visits/{later['id']}", json={'status': 'cancelled'}, headers=salon['oh']))
    assert early['late_cancel'] is False

    waived = ok(client.put(f"/visits/{soon['id']}", json={'late_cancel': False}, headers=salon['eh']))
    assert waived['late_cancel'] is False and waived['status'] == 'cancelled'
    event = db.query(BusinessAuditEvent).filter(BusinessAuditEvent.action == 'visit.updated').all()[-1]
    assert event.changes['late_cancel'] == {'old': True, 'new': False}

    marked = ok(client.put(f"/visits/{later['id']}", json={'late_cancel': True}, headers=salon['oh']))
    assert marked['late_cancel'] is True
    reopened = ok(client.put(f"/visits/{later['id']}", json={'status': 'planned'}, headers=salon['oh']))
    assert reopened['late_cancel'] is False
    response = client.put(f"/visits/{later['id']}", json={'late_cancel': True}, headers=salon['oh'])
    assert response.status_code == 422
    assert response.json()['error']['detail']['code'] == 'visit_late_cancel_not_cancelled'


def test_window_follows_the_salon_setting(client, db, salon):
    business = db.get(Business, salon['business']['id'])
    business.late_cancel_window_hours = 0
    db.commit()
    soon = salon['visit'](1)
    assert ok(client.put(f"/visits/{soon['id']}", json={'status': 'cancelled'},
                         headers=salon['oh']))['late_cancel'] is False
    business.late_cancel_window_hours = 96
    db.commit()
    later = salon['visit'](72)
    assert ok(client.put(f"/visits/{later['id']}", json={'status': 'cancelled'},
                         headers=salon['oh']))['late_cancel'] is True


def test_no_show_is_never_counted_as_earned_money(client, db, salon):
    from app.api.crud import crud_salary

    paid = salon['visit'](-5, status='completed', payment_method='cash')
    missed = salon['visit'](-3)
    ok(client.put(f"/visits/{missed['id']}", json={'status': 'no_show'}, headers=salon['oh']))

    employee = db.get(Employee, salon['employee']['id'])
    employee.base_salary_exact = Decimal('0')
    employee.base_salary_currency = 'EUR'
    employee.commission_percentage = 10
    db.commit()
    start = utcnow() - timedelta(days=1)
    calc = crud_salary.calculate_salary(db, employee.id, start, start + timedelta(days=2),
                                        calculated_by_id=salon['owner'].id, currency='EUR')
    assert calc.completed_visits == 1
    assert calc.total_revenue_exact == Decimal('100.00')

    payments = ok(client.get('/analytics/payments/statistics', headers=salon['oh']))
    assert payments['total_count'] == 1 and payments['total_payments'] == 100
    overview = ok(client.get(f"/clients/{salon['customer']['id']}/overview", headers=salon['oh']))
    assert overview['payments']['paid_visits'] == 1
    assert overview['payments']['total_paid'] == 100
    assert paid['id'] != missed['id']


def test_months_ago_clamps_to_the_end_of_a_short_month():
    assert attendance.months_ago(datetime(2026, 3, 31, 12), 1) == datetime(2026, 2, 28, 12)
    assert attendance.months_ago(datetime(2026, 1, 15), 13) == datetime(2024, 12, 15)


def test_legacy_presign_endpoint_is_gone(client, salon):
    # it signed any key and method for anyone with a specialist role; nothing calls it any more
    response = client.get('/files/presign', params={'filename': 'x.png', 'prefix': 'other', 'method': 'get_object'},
                          headers=salon['oh'])
    assert response.status_code == 404
    from app.main import app
    assert not any(getattr(route, 'path', '').startswith('/files') for route in app.routes)
