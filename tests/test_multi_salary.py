"""Commission follows the master's own service snapshots, not the chain's first master."""
from datetime import timedelta

import pytest

from app.db.models.visit import Visit
from tests.test_multi_service import _chain_body, _ok

pytest_plugins = ["tests.test_multi_service"]


def _calculate(client, salon, employee_id):
    return _ok(client.post(f'/employees/{employee_id}/salary/calculate', headers=salon['headers'], json={
        'period_start': (salon['start'] - timedelta(days=1)).isoformat(),
        'period_end': (salon['start'] + timedelta(days=1)).isoformat(),
    }), 'salary')


def _employee(client, salon, suffix):
    return _ok(client.post('/employees/create-with-user', headers=salon['headers'], json={
        'full_name': f'Payroll {suffix}', 'email': f'payroll.{suffix}@example.com',
        'phone': f'+42070100100{suffix}', 'commission_percentage': 10, 'base_salary': 100,
    }), 'employee')


def test_chain_commissions_belong_to_each_master_once(client, salon):
    first = _employee(client, salon, '1')
    second = _employee(client, salon, '2')
    _ok(client.post('/visits/', headers=salon['headers'], json=_chain_body(salon,
        specialist_id=first['employee_user_id'], status='completed', payment_method='cash', items=[
            {'service_id': salon['first']['id'], 'specialist_id': first['employee_user_id']},
            {'service_id': salon['second']['id'], 'specialist_id': second['employee_user_id']},
            {'service_id': salon['first']['id'], 'specialist_id': first['employee_user_id']},
        ])), 'chain')
    for employee, amount in [(first, 40.20), (second, 30.20)]:
        salary = _calculate(client, salon, employee['id'])
        assert salary['total_visits'] == 1
        assert salary['completed_visits'] == 1
        assert salary['total_revenue'] == pytest.approx(amount)
        assert salary['commission_amount'] == pytest.approx(amount * .1)
        assert salary['total_salary'] == pytest.approx(100 + amount * .1)


def test_payroll_excludes_deleted_visits_but_keeps_single_service_price(client, db, salon):
    employee = _employee(client, salon, '3')
    body = {'client_id': salon['client']['id'], 'specialist_id': employee['employee_user_id'],
            'service_id': salon['first']['id'], 'date_start': salon['start'].isoformat(),
            'price': 27, 'status': 'completed', 'payment_method': 'cash'}
    _ok(client.post('/visits/', headers=salon['headers'], json=body), 'single')
    deleted = _ok(client.post('/visits/', headers=salon['headers'], json={**body,
        'date_start': (salon['start'] + timedelta(hours=3)).isoformat(), 'price': 99}), 'deleted')
    row = db.get(Visit, deleted['id']); row.deleted_at = salon['start']; db.commit()
    salary = _calculate(client, salon, employee['id'])
    assert salary['total_visits'] == 1
    assert salary['total_revenue'] == 27
