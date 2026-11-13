from decimal import Decimal, localcontext

import pytest
from sqlalchemy import select, update

from app.api.core.payroll_money import PayrollError
from app.api.crud import crud_employee
from app.db.models.business import Business
from app.db.models.employee import Employee
from app.db.models.user import User
from app.schemas.employee import EmployeeCreate, EmployeeCreateWithUser, EmployeeUpdate
from tests import test_visit_financial_privacy as privacy

salon = privacy.salon


@pytest.fixture
def world(db, salon):
    db.execute(update(Business).where(Business.id == salon['business']['id']).values(currency='CZK'))
    db.commit()
    salon['employee_id'] = salon['employee']['id']
    return salon


def test_create_with_user_infers_fresh_business_unit(client, db, world):
    response = client.post('/employees/create-with-user', headers=world['oh'], json={
        'full_name': 'New Staff', 'email': 'new.staff@example.com', 'base_salary': 1000.01})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['base_salary_currency'] == 'CZK'
    assert Decimal(result['base_salary_exact']) == Decimal('1000.01')
    employee = db.get(Employee, result['id'])
    assert employee.base_salary_exact == Decimal('1000.01')


def test_amount_only_preserves_known_unit_after_business_switch(client, db, world):
    employee = db.get(Employee, world['employee_id'])
    employee.base_salary = 1000
    employee.base_salary_exact = Decimal('1000')
    employee.base_salary_currency = 'CZK'
    db.execute(update(Business).where(Business.id == world['business']['id']).values(currency='EUR'))
    db.commit()
    response = client.put(f'/employees/{employee.id}', headers=world['oh'], json={'base_salary': 40.01})
    assert response.status_code == 200, response.text
    assert response.json()['base_salary_currency'] == 'CZK'
    assert Decimal(response.json()['base_salary_exact']) == Decimal('40.01')


def test_legacy_unknown_amount_edit_remains_unknown(db, world):
    employee = db.get(Employee, world['employee_id'])
    employee.base_salary_currency = None
    employee.base_salary_exact = None
    employee.base_salary = 1000
    db.commit()
    updated = crud_employee.update_employee(db, employee, EmployeeUpdate(base_salary=40))
    assert updated.base_salary == 40
    assert updated.base_salary_currency is None and updated.base_salary_exact is None
    updated = crud_employee.update_employee(db, updated, EmployeeUpdate(base_salary=40, base_salary_currency='EUR'))
    assert updated.base_salary_exact == Decimal('40') and updated.base_salary_currency == 'EUR'


@pytest.mark.parametrize('body', [{'base_salary_currency': 'EUR'}, {'base_salary': 1.001}, {'base_salary': 10, 'base_salary_currency': 'ZZZ'}])
def test_invalid_update_is_atomic(client, db, world, body):
    employee = db.get(Employee, world['employee_id'])
    before = (employee.base_salary, employee.base_salary_exact, employee.base_salary_currency, employee.notes)
    response = client.put(f'/employees/{employee.id}', headers=world['oh'], json={**body, 'notes': 'must not persist'})
    assert response.status_code == 400, response.text
    db.expire_all()
    assert (employee.base_salary, employee.base_salary_exact, employee.base_salary_currency, employee.notes) == before


@pytest.mark.parametrize('body', [{'base_salary': 1.001}, {'base_salary': 10, 'base_salary_currency': 'ZZZ'}])
def test_invalid_create_with_user_never_commits_user(client, db, world, body):
    response = client.post('/employees/create-with-user', headers=world['oh'], json={
        'full_name': 'Invalid Staff', 'email': 'invalid.staff@example.com', **body})
    assert response.status_code == 400, response.text
    assert db.execute(select(User.id).where(User.email == 'invalid.staff@example.com')).first() is None


def test_exact_terms_do_not_depend_on_decimal_context(db, world):
    employee = db.get(Employee, world['employee_id'])
    employee.base_salary_currency = 'EUR'
    employee.base_salary_exact = Decimal('0')
    db.commit()
    with localcontext() as context:
        context.prec = 2
        updated = crud_employee.update_employee(db, employee, EmployeeUpdate(base_salary=1000.01))
    assert updated.base_salary_exact == Decimal('1000.01')


def test_input_rates_keep_digits_until_existing_float_boundary():
    value = Decimal('12.123456789123456789')
    for model, kwargs in [(EmployeeCreate, {'employee_user_id': 1}),
                          (EmployeeCreateWithUser, {'full_name': 'A'}), (EmployeeUpdate, {})]:
        assert model(commission_percentage=value, **kwargs).commission_percentage == value


def test_profile_pair_and_foreign_specialist_access(client, db, world, make_user, headers_for):
    employee = db.get(Employee, world['employee_id'])
    employee.base_salary_currency = 'CZK'
    employee.base_salary_exact = Decimal('123')
    employee.base_salary = 123
    db.commit()
    for path in [f'/employees/{employee.id}', '/employees/', f'/employees/by-user/{employee.employee_user_id}']:
        response = client.get(path, headers=world['oh'])
        assert response.status_code == 200, response.text
        item = response.json()[0] if isinstance(response.json(), list) else response.json()
        assert item['base_salary_currency'] == 'CZK' and Decimal(item['base_salary_exact']) == 123
    foreign = make_user('specialist')
    response = client.get(f'/employees/by-user/{employee.employee_user_id}', headers=headers_for(foreign))
    assert response.status_code == 403, response.text


def test_current_salary_selects_currency_and_frozen_percentage(client, db, world):
    from datetime import datetime, timedelta
    from app.api.crud import crud_salary

    employee = db.get(Employee, world['employee_id'])
    employee.base_salary = 100
    employee.base_salary_currency = 'CZK'
    employee.base_salary_exact = Decimal('100')
    employee.commission_percentage = 12.3456789
    db.commit()
    start = datetime(2026, 1, 1)
    for unit in ['CZK', 'EUR']:
        crud_salary.calculate_salary(db, employee.id, start, start + timedelta(days=30),
                                    calculated_by_id=world['owner'].id, currency=unit)
    employee.commission_percentage = 80
    db.commit()
    headers = world['eh']
    for language in ['en', 'ru', 'cz']:
        response = client.get('/employees/me/salary/current', headers={**headers, 'Accept-Language': language})
        assert response.status_code == 400, response.text
        assert response.json()['error']['detail']['reason'] == 'payroll_currency_required'
        assert response.json()['error']['message'] != 'payroll_currency_required'
        assert response.headers['cache-control'] == 'no-store'
    response = client.get('/employees/me/salary/current?currency=CZK', headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()['currency'] == 'CZK'
    assert response.json()['commission_percentage'] == 12.3456789
    assert Decimal(response.json()['salary_period']['commission_percentage']) == Decimal('12.3456789')


def test_hire_existing_user_and_fire_return_exact_pair(client, db, world, make_user):
    worker = make_user('specialist', business_id=world['business']['id'])
    response = client.post('/employees/', headers=world['oh'], json={
        'employee_user_id': worker.id, 'base_salary': 123.45, 'base_salary_currency': 'EUR'})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['base_salary_currency'] == 'EUR'
    assert Decimal(result['base_salary_exact']) == Decimal('123.45')
    response = client.delete(f"/employees/{result['id']}", headers=world['oh'])
    assert response.status_code == 200, response.text
    assert response.json()['base_salary_currency'] == 'EUR'
    assert Decimal(response.json()['base_salary_exact']) == Decimal('123.45')


def test_amount_update_refreshes_concurrent_saved_unit(db, world):
    from sqlalchemy.orm import Session

    cached = db.get(Employee, world['employee_id'])
    cached.base_salary_currency = 'EUR'
    cached.base_salary_exact = Decimal('10')
    db.commit()
    assert cached.base_salary_currency == 'EUR'
    with Session(db.get_bind()) as writer:
        writer.execute(update(Employee).where(Employee.id == cached.id).values(
            base_salary_currency='CZK', base_salary_exact=Decimal('20'), base_salary=20))
        writer.commit()
    assert cached.base_salary_currency == 'EUR'
    updated = crud_employee.update_employee(db, cached, EmployeeUpdate(base_salary=30.01))
    assert updated.base_salary_currency == 'CZK' and updated.base_salary_exact == Decimal('30.01')


def test_term_errors_are_localized_without_partial_writes(client, db, world):
    for language in ['en', 'ru', 'cz']:
        response = client.put(f"/employees/{world['employee_id']}",
                              headers={**world['oh'], 'Accept-Language': language},
                              json={'base_salary_currency': 'EUR'})
        assert response.status_code == 400, response.text
        detail = response.json()['error']['detail']
        assert detail['reason'] == 'payroll_invalid_amount'
        assert response.json()['error']['message'] != detail['reason']
        assert response.headers['cache-control'] == 'no-store'


def test_new_nonzero_salary_requires_known_unit_but_zero_solo_is_allowed(db, make_user):
    owner = make_user('business_owner')
    worker = make_user('specialist')
    before = db.query(Employee).count()
    with pytest.raises(PayrollError, match='payroll_currency_review'):
        crud_employee.create_employee(db, EmployeeCreate(employee_user_id=worker.id, base_salary=100), owner.id)
    db.rollback()
    assert db.query(Employee).count() == before
    created = crud_employee.create_employee(db, EmployeeCreate(employee_user_id=worker.id, base_salary=0), owner.id)
    assert created.base_salary_currency is None and created.base_salary_exact is None


def test_create_with_user_reuses_validated_unit_after_user_commit(client, db, world, monkeypatch):
    from app.api.crud import crud_user

    original = crud_user.create_user

    def create_then_change_business(*args, **kwargs):
        user = original(*args, **kwargs)
        db.execute(update(Business).where(Business.id == world['business']['id']).values(currency='ZZZ'))
        db.commit()
        return user

    monkeypatch.setattr(crud_user, 'create_user', create_then_change_business)
    response = client.post('/employees/create-with-user', headers=world['oh'], json={
        'full_name': 'Frozen Terms Staff', 'email': 'frozen.terms@example.com', 'base_salary': 100})
    assert response.status_code == 200, response.text
    assert response.json()['base_salary_currency'] == 'CZK'
    assert Decimal(response.json()['base_salary_exact']) == 100


@pytest.mark.parametrize('path', ['/employees/create-with-user', '/employees/'])
def test_explicit_nulls_from_the_web_form_mean_no_salary_terms_on_create(client, db, world, path):
    # the employee form sends null for an empty commission/salary field; the old API took that as 0
    body = {'commission_percentage': None, 'base_salary': None}
    if path == '/employees/create-with-user':
        body.update(full_name='Null Terms', email='null.terms@example.com')
    else:
        from app.db.models.user import User as UserModel
        body.update(employee_user_id=db.execute(select(UserModel.id).where(
            UserModel.email == 'privacy.staff@example.com')).scalar_one(), position='Barber')
        db.execute(update(Employee).values(is_active=False))
        db.commit()
    response = client.post(path, headers=world['oh'], json=body)
    assert response.status_code == 200, response.text
    assert response.json()['commission_percentage'] == 0
    assert response.json()['base_salary'] == 0


def test_explicit_null_on_update_keeps_existing_terms(client, db, world):
    employee = db.get(Employee, world['employee_id'])
    employee.commission_percentage = 12.5
    db.commit()
    response = client.put(f'/employees/{employee.id}', headers=world['oh'],
                          json={'commission_percentage': None, 'base_salary': None, 'position': 'Senior'})
    assert response.status_code == 200, response.text
    assert response.json()['commission_percentage'] == 12.5 and response.json()['position'] == 'Senior'
