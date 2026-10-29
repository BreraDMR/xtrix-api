"""Known payroll units are explicit snapshots; historical floats stay unlabelled."""
from datetime import datetime
from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db.models.employee import Employee
from app.db.models.salary_calculation import SalaryCalculation

FIELDS = ('total_revenue', 'commission_amount', 'base_salary', 'total_salary',
          'adjustments_total', 'total_with_adjustments', 'paid_amount')


@pytest.fixture
def payroll(db, make_user):
    from app.db.models.business import Business
    owner = make_user('business_owner')
    worker = make_user('specialist')
    business = Business(name='Payroll unit', business_type='salon')
    db.add(business); db.flush()
    employee = Employee(business_owner_id=owner.id, employee_user_id=worker.id, base_salary=17.25123)
    db.add(employee); db.flush()
    calc = SalaryCalculation(employee_id=employee.id, period_start=datetime(2026, 10, 1),
                             period_end=datetime(2026, 11, 1), total_revenue=91.23456)
    db.add(calc); db.commit()
    return employee.id, calc.id, business.id, owner.id


def test_new_fields_are_nullable_and_exact(db):
    columns = {c['name']: c for c in inspect(db.get_bind()).get_columns('salary_calculations')}
    for field in FIELDS:
        col = columns[field + '_exact']
        assert col['nullable'] and (col['type'].precision, col['type'].scale) == (18, 2)
    assert columns['currency']['nullable'] and columns['business_id_snapshot']['nullable']
    assert columns['salary_period_id']['nullable']
    period_columns={c['name']:c for c in inspect(db.get_bind()).get_columns('salary_periods')}
    assert period_columns['commission_percentage']['type'].scale is None
    from app.schemas.salary import SalaryCalculationOut
    assert SalaryCalculationOut.model_fields['salary_period_id'].default is None
    assert SalaryCalculationOut.model_fields['salary_period'].default is None


def test_legacy_roundtrip_does_not_guess_units(db, payroll):
    employee, calc, _, _ = payroll
    from app.db.models.salary_adjustment import SalaryAdjustment
    from app.db.models.salary_payment import SalaryPayment
    from app.db.models.expense import Expense
    _, _, business, owner = payroll
    db.add(SalaryAdjustment(salary_calculation_id=calc, type='bonus', amount=-1.234567, created_by_id=owner))
    db.add(SalaryPayment(salary_calculation_id=calc, amount=2.345678, payment_date=datetime(2026,10,29), created_by_id=owner))
    db.add(Expense(business_id=business, amount=3.456789, currency='EUR', expense_date=datetime(2026,10,29)))
    for index, name in enumerate(FIELDS):
        setattr(db.get(SalaryCalculation,calc),name,10.123456+index)
    db.commit();db.rollback()
    old_columns = {}
    tables = ('employees','salary_calculations','salary_adjustments','salary_payments','expenses')
    for table in tables:
        old_columns[table] = [c['name'] for c in inspect(db.get_bind()).get_columns(table)
            if c['name'] not in {'base_salary_currency','business_id_snapshot','salary_period_id','salary_payment_id'}
            and not c['name'].endswith('_exact') and not (table.startswith('salary_') and c['name']=='currency')]
    def snapshot():
        with db.get_bind().connect() as conn:
            return {table: [dict(r) for r in conn.execute(text('SELECT '+','.join(old_columns[table])+f' FROM {table} ORDER BY id')).mappings()] for table in tables}
    before=snapshot()
    try:
        for _ in range(2):
            command.downgrade(Config('alembic.ini'), '0085_loyalty_discount_snapshot')
            assert snapshot()==before
            command.upgrade(Config('alembic.ini'), 'head')
            assert snapshot()==before
            db.expire_all()
            assert db.get(Employee, employee).base_salary == 17.25123
            row = db.get(SalaryCalculation, calc)
            assert row.total_revenue == 10.123456 and row.currency is None
            assert all(getattr(row, f + '_exact') is None for f in FIELDS)
            db.rollback()
    finally:
        command.upgrade(Config('alembic.ini'), 'head')


def known(db, payroll):
    employee, calc, business, owner = payroll
    from app.db.models.salary_period import SalaryPeriod
    period=SalaryPeriod(business_id=business,employee_id=employee,period_start=datetime(2026,10,1),
        period_end=datetime(2026,11,1),base_salary_currency='CZK',base_salary_exact=20,
        commission_percentage=Decimal('10.1234567890123456789'),created_by_id=owner)
    db.add(period);db.flush()
    period_id=period.id
    values = dict(total_revenue=100, commission_amount=10, base_salary=20, total_salary=30,
                  adjustments_total=-5, total_with_adjustments=25, paid_amount=5)
    db.execute(text('UPDATE salary_calculations SET salary_period_id=:period, currency=\'CZK\', business_id_snapshot=:business, '
                    + ', '.join(f'{f}_exact=:{f}' for f in FIELDS) + ' WHERE id=:id'),
               dict(values, business=business, id=calc,period=period_id))
    db.commit()
    return period_id


@pytest.mark.parametrize('change', ["currency='czk'", "currency='ČZK'", "currency=NULL", "salary_period_id=NULL", 'business_id_snapshot=NULL',
    'base_salary_exact=NULL', "paid_amount_exact='NaN'", 'paid_amount_exact=26',
    'total_salary_exact=31', 'total_with_adjustments_exact=24', 'total_revenue_exact=-1'])
def test_calculation_rejects_partial_invalid_or_inconsistent_bundle(db, payroll, change):
    known(db, payroll)
    with pytest.raises(IntegrityError):
        db.execute(text(f'UPDATE salary_calculations SET {change}')); db.commit()
    db.rollback()


def test_known_snapshot_blocks_lossy_downgrade(db, payroll):
    known(db, payroll); db.rollback()
    with pytest.raises(DBAPIError, match='Payroll currency data exists'):
        command.downgrade(Config('alembic.ini'), '0085_loyalty_discount_snapshot')
    db.expire_all()
    assert db.get(SalaryCalculation, payroll[1]).paid_amount_exact == Decimal('5.00')


def test_readonly_fields_and_optional_request_currency():
    from app.schemas.employee import EmployeeCreate, EmployeeOut, EmployeeUpdate
    from app.schemas.salary import (CalculateSalaryRequest, SalaryAdjustmentCreate, SalaryAdjustmentOut,
                                    SalaryCalculationOut, SalaryPaymentCreate, SalaryPaymentOut)
    assert EmployeeOut.model_fields['base_salary_exact'].default is None
    assert EmployeeOut.model_fields['base_salary_currency'].default is None
    assert SalaryCalculationOut.model_fields['currency'].default is None
    for schema in (SalaryAdjustmentOut, SalaryPaymentOut):
        assert schema.model_fields['amount_exact'].default is None
    assert EmployeeCreate.model_fields['base_salary_currency'].default is None
    assert EmployeeUpdate.model_fields['base_salary_currency'].default is None
    for schema in (EmployeeCreate, EmployeeUpdate, SalaryAdjustmentCreate, SalaryPaymentCreate):
        assert 'amount_exact' not in schema.model_fields and 'base_salary_exact' not in schema.model_fields
    body = dict(period_start='2026-10-01T00:00:00Z', period_end='2026-11-01T00:00:00Z')
    assert CalculateSalaryRequest(**body).currency is None
    assert CalculateSalaryRequest(**body, currency='CZK').currency == 'CZK'
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        CalculateSalaryRequest(**body, currency='czk')


@pytest.mark.parametrize('table,column,updates', [
    ('employees', 'base_salary_currency', "base_salary_currency='EUR'"),
    ('employees', 'base_salary_exact', "base_salary_exact=1"),
    ('employees', 'base_salary_exact', "base_salary_currency='EUR',base_salary_exact=-1"),
    ('salary_adjustments', 'amount_exact', "currency='EUR',amount_exact='NaN'"),
    ('salary_adjustments', 'amount_exact', "currency='EUR'"),
    ('salary_payments', 'amount_exact', "currency='EUR',amount_exact=-1"),
    ('salary_payments', 'amount_exact', "currency='eur',amount_exact=1"),
])
def test_pairs_reject_incomplete_or_invalid_values(db, payroll, table, column, updates):
    _, calc, _, owner = payroll
    if table != 'employees':
        db.execute(text(f"INSERT INTO {table}(salary_calculation_id,amount,created_by_id,created_at"
                        + (',type' if table == 'salary_adjustments' else ',payment_date,payment_method,status,updated_at')
                        + ') VALUES (:calc,1,:owner,now()'
                        + (",'bonus')" if table == 'salary_adjustments' else ",now(),'cash','completed',now())")),
                   dict(calc=calc, owner=owner))
        db.commit()
    with pytest.raises(IntegrityError):
        db.execute(text(f'UPDATE {table} SET {updates}')); db.commit()
    db.rollback()


def test_payment_expense_link_is_unique_and_restricts_delete(db, payroll):
    _, calc, business, owner = payroll
    payment = db.execute(text("INSERT INTO salary_payments(salary_calculation_id,amount,"
        "payment_date,payment_method,status,created_by_id,created_at,updated_at) "
        "VALUES (:calc,5,now(),'cash','completed',:owner,now(),now()) RETURNING id"),
        dict(calc=calc,owner=owner)).scalar_one()
    sql = text("INSERT INTO expenses(business_id,amount,currency,expense_date,payment_method,salary_payment_id,created_at,updated_at) "
               "VALUES (:business,5,'CZK',now(),'cash',:payment,now(),now())")
    db.execute(sql, dict(business=business,payment=payment));db.commit()
    with pytest.raises(IntegrityError):
        db.execute(sql, dict(business=business,payment=payment));db.commit()
    db.rollback()
    with pytest.raises(IntegrityError):
        db.execute(text('DELETE FROM salary_payments WHERE id=:id'),dict(id=payment));db.commit()
    db.rollback()
    with pytest.raises(DBAPIError, match='Payroll currency data exists'):
        command.downgrade(Config('alembic.ini'),'0085_loyalty_discount_snapshot')


def test_payroll_roundtrip_preserves_prior_known_wallet_and_discount_data(db, monkeypatch):
    from app.api.core import loyalty_logic
    from tests import test_loyalty_visit_quote_lock as quote
    w = quote.world.__wrapped__(db, monkeypatch)
    assert loyalty_logic.use_points_for_visit(db,w['client'],w['business'],5,3,visit_id=w['visit'])[0]
    bind=db.get_bind();db.rollback()
    def snapshot():
        with bind.connect() as conn:
            return {table: [dict(r) for r in conn.execute(text(f'SELECT * FROM {table} ORDER BY id')).mappings()]
                    for table in ('client_cashback_balances','loyalty_transactions')}
    before=snapshot()
    try:
        for _ in range(2):
            command.downgrade(Config('alembic.ini'),'0085_loyalty_discount_snapshot')
            assert snapshot()==before
            command.upgrade(Config('alembic.ini'),'head')
            assert snapshot()==before
    finally:
        command.upgrade(Config('alembic.ini'),'head')


def test_period_table_starts_empty_without_backfill(db, payroll):
    assert inspect(db.get_bind()).has_table('salary_periods')
    assert db.execute(text('SELECT count(*) FROM salary_periods')).scalar_one()==0


def period_row(db, payroll, **changes):
    from app.db.models.salary_period import SalaryPeriod
    employee, _, business, owner=payroll
    values=dict(business_id=business,employee_id=employee,period_start=datetime(2026,10,1),
        period_end=datetime(2026,11,1),base_salary_currency=None,base_salary_exact=0,
        commission_percentage=Decimal('12.345678901234567890123456789'),created_by_id=owner)
    values.update(changes)
    row=SalaryPeriod(**values);db.add(row);db.commit();return row


def test_currency_calculations_share_one_period_and_one_snapshot(db, payroll):
    from app.db.models.salary_period import SalaryPeriod
    period_id=known(db,payroll)
    _,calc,business,_=payroll
    first=db.get(SalaryCalculation,calc)
    second=SalaryCalculation(employee_id=first.employee_id,period_start=first.period_start,
        period_end=first.period_end,salary_period_id=period_id,business_id_snapshot=business,
        currency='EUR',total_revenue_exact=100,commission_amount_exact=10,
        base_salary_exact=0,total_salary_exact=10,adjustments_total_exact=0,
        total_with_adjustments_exact=10,paid_amount_exact=0)
    db.add(second);db.commit()
    assert first.salary_period_id==second.salary_period_id
    assert db.query(SalaryPeriod).count()==1
    assert db.get(SalaryPeriod,period_id).commission_percentage==Decimal('10.1234567890123456789')
    assert second.base_salary_exact==0 and first.base_salary_exact==20
    with pytest.raises(IntegrityError):
        second.currency='CZK';db.commit()
    db.rollback()


@pytest.mark.parametrize('unit,amount', [(None,0),('EUR',0),('CZK',20)])
def test_period_accepts_zero_unknown_base_or_an_explicit_unit(db,payroll,unit,amount):
    row=period_row(db,payroll,base_salary_currency=unit,base_salary_exact=amount)
    assert row.base_salary_currency==unit and row.base_salary_exact==amount
    assert row.commission_percentage==Decimal('12.345678901234567890123456789')


@pytest.mark.parametrize('changes', [dict(base_salary_exact=1),dict(base_salary_exact=-1),
    dict(base_salary_currency='eur'),dict(base_salary_currency='ČZK'),dict(base_salary_exact='NaN'),
    dict(commission_percentage='NaN'),dict(commission_percentage=101),dict(commission_percentage=-1),
    dict(period_end=datetime(2026,10,1))])
def test_period_rejects_invalid_policy_and_date_range(db,payroll,changes):
    with pytest.raises(IntegrityError):period_row(db,payroll,**changes)
    db.rollback()


def test_one_policy_per_business_employee_period_and_lossy_rollback_is_refused(db,payroll):
    row=period_row(db,payroll)
    with pytest.raises(IntegrityError):period_row(db,payroll)
    db.rollback()
    assert db.query(SalaryCalculation).one().salary_period_id is None
    with pytest.raises(DBAPIError,match='Payroll currency data exists'):
        command.downgrade(Config('alembic.ini'),'0085_loyalty_discount_snapshot')
    db.expire_all()
    from app.db.models.salary_period import SalaryPeriod
    assert db.get(SalaryPeriod,row.id) is not None
