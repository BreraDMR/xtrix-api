from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal, localcontext, Inexact, Rounded
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.api.core.payroll_money import PayrollError, amount, currency
from app.api.crud import crud_salary as payroll, crud_expense
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.employee import Employee
from app.db.models.expense import Expense
from app.db.models.salary_calculation import SalaryCalculation
from app.db.models.salary_payment import SalaryPayment
from app.db.models.salary_period import SalaryPeriod
from app.db.models.visit import Visit
from app.db.models.visit_item import VisitItem
from app.db.session import SessionLocal
from app.schemas.salary import SalaryPaymentCreate, SalaryCalculationUpdate, SalaryPaymentUpdate, SalaryAdjustmentCreate

START = datetime(2026, 1, 1)
END = START + timedelta(days=30)


@pytest.fixture
def payroll_setup(db, make_user):
    owner = make_user('business_owner')
    worker = make_user('employee')
    business = Business(name='Payroll', business_type='salon', currency='CZK')
    db.add(business); db.flush(); owner.business_id = business.id
    employee = Employee(business_owner_id=owner.id, employee_user_id=worker.id,
        base_salary=100, base_salary_exact=Decimal('100'), base_salary_currency='CZK', commission_percentage=10)
    client = Client(full_name='Payroll client')
    db.add_all([employee, client]); db.commit()
    return SimpleNamespace(owner=owner, worker=worker, business=business, employee=employee, client=client)


def visit(db, setup, unit='CZK', price=10, items=None, **kwargs):
    row = Visit(business_id=setup.business.id, specialist_id=setup.worker.id,
        client_id=setup.client.id, date_start=START+timedelta(days=1, hours=db.query(Visit).count()*4), status='completed',
        price=price, currency_snapshot=unit, **kwargs)
    db.add(row); db.flush()
    for index, (specialist, value) in enumerate(items or []):
        db.add(VisitItem(visit_id=row.id, position=index, specialist_id=specialist,
            price=value, duration_minutes=30, date_start=row.date_start, date_end=row.date_start+timedelta(minutes=30)))
    db.commit(); db.expire_all()
    return row


def calculate(db, setup, unit=None, start=START, end=END):
    return payroll.calculate_salary(db, setup.employee.id, start, end,
        calculated_by_id=setup.owner.id, currency=unit)


def payment(value=10, status='completed'):
    return SalaryPaymentCreate(amount=value, payment_date=START, status=status)


def test_money_low_context():
    with localcontext() as ctx:
        ctx.prec = 2
        ctx.traps[Inexact] = True
        ctx.traps[Rounded] = True
        assert amount('1000.01') == Decimal('1000.01')
        assert amount(Decimal('0e999999')) == Decimal('0.00')
        assert amount('-0.01', signed=True) == Decimal('-0.01')
    with pytest.raises(PayrollError):
        currency('ZZZ')
    with pytest.raises(PayrollError):
        amount('1e-999999')


def test_full_own_items_split_units_and_frozen_period(db, payroll_setup):
    s=payroll_setup
    visit(db,s,price=1,items=[(s.worker.id,Decimal('20.10')),(s.owner.id,500),(s.worker.id,Decimal('30.20'))])
    visit(db,s,'EUR',price=20)
    visit(db,s,price=900,deleted_at=START)
    with pytest.raises(PayrollError,match='payroll_currency_required'):
        calculate(db,s)
    db.rollback()
    first=calculate(db,s,'CZK')
    assert first.total_revenue_exact == Decimal('50.30')
    assert first.total_salary_exact == Decimal('105.03')
    s.employee.base_salary_exact=999; s.employee.base_salary_currency='EUR'; s.employee.commission_percentage=80; db.commit()
    second=calculate(db,s,'EUR')
    assert second.total_salary_exact == Decimal('2.00')
    assert second.salary_period_id == first.salary_period_id
    repeated=calculate(db,s,'CZK')
    assert repeated.id == first.id and repeated.base_salary_exact == 100
    assert db.query(SalaryPeriod).count()==1


def test_unknown_completed_requires_review_even_explicit_other_currency(db,payroll_setup):
    row=visit(db,payroll_setup,None)
    with pytest.raises(PayrollError) as exc:
        calculate(db,payroll_setup,'EUR')
    assert exc.value.reason=='payroll_visit_review' and exc.value.context['visit_ids']==[row.id]
    db.rollback()
    assert db.query(SalaryCalculation).count()==0


def test_legacy_and_overlap_are_preserved(db,payroll_setup):
    s=payroll_setup
    legacy=SalaryCalculation(employee_id=s.employee.id,period_start=START,period_end=END)
    db.add(legacy);db.commit()
    with pytest.raises(PayrollError,match='payroll_legacy_review'): calculate(db,s,'CZK')
    db.rollback()
    with pytest.raises(PayrollError,match='payroll_legacy_review'):
        payroll.create_salary_payment(db,legacy.id,payment(),s.owner.id)
    db.refresh(legacy)
    assert legacy.currency is None and legacy.total_salary_exact is None
    db.delete(legacy);db.commit()
    calculate(db,s,'CZK')
    with pytest.raises(PayrollError,match='payroll_period_overlap'):
        calculate(db,s,'EUR',start=START+timedelta(days=1))
    db.rollback()


def test_payment_expense_atomic_and_settled_immutable(db,payroll_setup):
    s=payroll_setup;calc=calculate(db,s,'CZK')
    paid=payroll.create_salary_payment(db,calc.id,payment(40),s.owner.id)
    expense=db.query(Expense).filter_by(salary_payment_id=paid.id).one()
    assert expense.currency=='CZK' and expense.amount==40
    db.refresh(calc);assert calc.paid_amount_exact==40 and calc.payment_status=='partial'
    for call in [lambda:payroll.update_salary_payment(db,paid,SalaryPaymentUpdate(amount=1)),
        lambda:payroll.delete_salary_payment(db,paid.id),lambda:calculate(db,s,'CZK'),
        lambda:payroll.update_salary_calculation(db,calc,SalaryCalculationUpdate(is_paid=True)),
        lambda:crud_expense.delete_expense(db,expense.id)]:
        with pytest.raises(PayrollError):call()
        db.rollback()
    adj=payroll.create_salary_adjustment(db,calc.id,SalaryAdjustmentCreate(type='deduction',amount=-60),s.owner.id)
    db.refresh(calc);assert calc.payment_status=='paid' and calc.total_with_adjustments_exact==40
    with pytest.raises(PayrollError):payroll.delete_salary_adjustment(db,adj.id)
    db.rollback()


@pytest.mark.parametrize('failure',['immediate','deferred'])
def test_expense_failure_rolls_back_actual_payment(db,payroll_setup,monkeypatch,failure):
    s=payroll_setup;calc=calculate(db,s,'CZK'); calc_id=calc.id
    if failure=='immediate':
        def fail(*args,**kwargs):raise RuntimeError('injected expense failure')
        monkeypatch.setattr(crud_expense,'create_expense',fail)
    else:
        db.execute(text("""CREATE FUNCTION reject_payroll_expense() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'deferred expense failure'; END; $$"""))
        db.execute(text("""CREATE CONSTRAINT TRIGGER reject_payroll_expense AFTER INSERT ON expenses DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION reject_payroll_expense()"""));db.commit()
    try:
        with pytest.raises(Exception):payroll.create_salary_payment(db,calc_id,payment(25),s.owner.id)
        assert db.query(SalaryPayment).count()==0 and db.query(Expense).count()==0
        db.expire_all();assert db.get(SalaryCalculation,calc_id).paid_amount_exact==0
    finally:
        db.rollback()
        if failure=='deferred':
            db.execute(text('DROP TRIGGER reject_payroll_expense ON expenses'))
            db.execute(text('DROP FUNCTION reject_payroll_expense()'));db.commit()


def test_pending_completion_creates_one_expense(db,payroll_setup):
    s=payroll_setup;calc=calculate(db,s,'CZK')
    row=payroll.create_salary_payment(db,calc.id,payment(30,'pending'),s.owner.id)
    assert db.query(Expense).count()==0
    payroll.update_salary_payment(db,row,SalaryPaymentUpdate(status='completed'))
    assert db.query(Expense).count()==1
    db.refresh(calc);assert calc.paid_amount_exact==30


def test_concurrent_currency_calculations_share_one_base(db,payroll_setup):
    s=payroll_setup;ids=(s.employee.id,s.owner.id);db.commit();barrier=Barrier(2)
    def run(unit):
        with SessionLocal() as session:
            barrier.wait()
            calc=payroll.calculate_salary(session,ids[0],START,END,ids[1],currency=unit)
            return calc.id,calc.salary_period_id,calc.base_salary_exact
    with ThreadPoolExecutor(2) as pool:rows=list(pool.map(run,['CZK','EUR']))
    assert rows[0][1]==rows[1][1] and sum(r[2] for r in rows)==100
    assert db.query(SalaryPeriod).count()==1


def test_concurrent_payments_cannot_exceed_remaining(db,payroll_setup):
    s=payroll_setup;calc=calculate(db,s,'CZK');ids=(calc.id,s.owner.id);db.commit();barrier=Barrier(2)
    def run(_):
        with SessionLocal() as session:
            barrier.wait()
            try:
                payroll.create_salary_payment(session,ids[0],payment(70),ids[1]);return 'paid'
            except PayrollError as exc:return exc.reason
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(run,range(2)))
    assert sorted(results)==['paid','payroll_payment_exceeds_remaining']
    db.expire_all();assert db.get(SalaryCalculation,ids[0]).paid_amount_exact==70
    assert db.query(SalaryPayment).count()==1 and db.query(Expense).count()==1


def test_concurrent_same_currency_reuses_calculation(db,payroll_setup):
    s=payroll_setup;ids=(s.employee.id,s.owner.id);db.commit();barrier=Barrier(2)
    def run(_):
        with SessionLocal() as session:
            barrier.wait()
            row=payroll.calculate_salary(session,ids[0],START,END,ids[1],currency='CZK')
            return row.id
    with ThreadPoolExecutor(2) as pool:rows=list(pool.map(run,range(2)))
    assert rows[0]==rows[1] and db.query(SalaryCalculation).count()==1


def test_currency_inputs_pending_delete_and_adjustment_floor(db,payroll_setup):
    s=payroll_setup;calc=calculate(db,s,'CZK')
    with pytest.raises(PayrollError,match='payroll_currency_mismatch'):
        payroll.create_salary_payment(db,calc.id,SalaryPaymentCreate(amount='1.01',currency='EUR',payment_date=START),s.owner.id)
    row=payroll.create_salary_payment(db,calc.id,payment('1.01','pending'),s.owner.id)
    assert row.amount_exact==Decimal('1.01')
    assert payroll.delete_salary_payment(db,row.id)
    with pytest.raises(PayrollError,match='payroll_total_below_paid'):
        payroll.create_salary_adjustment(db,calc.id,SalaryAdjustmentCreate(type='deduction',amount=-101),s.owner.id)
    db.rollback();db.refresh(calc)
    assert calc.total_with_adjustments_exact==100


def test_current_requires_currency_when_multiple_units(db,payroll_setup):
    s=payroll_setup;calculate(db,s,'CZK');calculate(db,s,'EUR')
    with pytest.raises(PayrollError,match='payroll_currency_required'):
        payroll.get_current_unpaid_salary(db,employee_id=s.employee.id)
    row=payroll.get_current_unpaid_salary(db,employee_id=s.employee.id,currency='CZK')
    assert row.currency=='CZK'


def test_http_errors_localized_and_legacy_notes_allowed(db,payroll_setup,client,headers_for):
    s=payroll_setup;headers={**headers_for(s.owner),'Accept-Language':'ru'}
    response=client.post(f'/employees/{s.employee.id}/salary/calculate',headers=headers,json={
        'period_start':START.isoformat(),'period_end':END.isoformat(),'currency':'CZK'})
    assert response.status_code==200,response.text
    calc_id=response.json()['id']
    response=client.put(f'/employees/{s.employee.id}/salary/{calc_id}',headers=headers,json={'is_paid':True})
    assert response.status_code==400 and response.json()['error']['detail']['reason']=='payroll_payment_required'
    assert 'Для изменения' in response.json()['error']['message']
    assert response.headers.get('cache-control')=='no-store'
    legacy=SalaryCalculation(employee_id=s.employee.id,period_start=END,period_end=END+timedelta(days=1))
    db.add(legacy);db.commit()
    payroll.update_salary_calculation(db,legacy,SalaryCalculationUpdate(notes='review needed',is_paid=False))
    assert legacy.notes=='review needed' and legacy.currency is None


def test_http_expense_failure_is_typed_and_atomic(db,payroll_setup,client,headers_for,monkeypatch):
    s=payroll_setup;calc=calculate(db,s,'CZK')
    def fail(*args,**kwargs):raise RuntimeError('secret database diagnostic must not escape')
    monkeypatch.setattr(crud_expense,'create_expense',fail)
    response=client.post(f'/employees/{s.employee.id}/salary/{calc.id}/payments',
        headers={**headers_for(s.owner),'Accept-Language':'ru'},json={
            'amount':'20.01','currency':'CZK','payment_date':START.isoformat()})
    assert response.status_code==400,response.text
    assert response.json()['error']['detail']['reason']=='payroll_transaction_failed'
    assert 'secret database' not in response.text
    assert db.query(SalaryPayment).count()==0 and db.query(Expense).count()==0
    db.refresh(calc);assert calc.paid_amount_exact==0


def test_low_context_mutations_preserve_cents(db,payroll_setup):
    s=payroll_setup;s.employee.base_salary_exact=Decimal('100.01');db.commit()
    visit(db,s,price=Decimal('10.05'))
    with localcontext() as ctx:
        ctx.prec=2;ctx.traps[Inexact]=True;ctx.traps[Rounded]=True
        calc=calculate(db,s,'CZK')
        assert calc.commission_amount_exact==Decimal('1.01') and calc.total_salary_exact==Decimal('101.02')
        row=payroll.create_salary_payment(db,calc.id,payment('40.01','pending'),s.owner.id)
        payroll.update_salary_payment(db,row,SalaryPaymentUpdate(amount='50.01',status='completed'))
        payroll.create_salary_adjustment(db,calc.id,SalaryAdjustmentCreate(type='deduction',amount='-0.01'),s.owner.id)
        with pytest.raises(PayrollError,match='payroll_payment_exceeds_remaining'):
            payroll.create_salary_payment(db,calc.id,payment('51.01'),s.owner.id)
    db.refresh(calc)
    assert calc.total_with_adjustments_exact==Decimal('101.01') and calc.paid_amount_exact==Decimal('50.01')


def test_rate_rounding_uses_all_numeric_digits():
    # The number is just below a half-cent boundary; a short precision rounds it up too early.
    rate_value=Decimal('0.499'+'9'*120)
    with localcontext() as ctx:
        ctx.prec=2;ctx.traps[Inexact]=True;ctx.traps[Rounded]=True
        assert payroll._commission(Decimal('1.00'),rate_value)==Decimal('0.00')
        assert payroll._commission(Decimal('1.00'),Decimal('0.5'))==Decimal('0.01')


@pytest.mark.parametrize('corruption',['employee','business','dates','base','rate'])
def test_period_bundle_mismatch_blocks_money(db,payroll_setup,make_user,corruption):
    s=payroll_setup;calc=calculate(db,s,'CZK');period=db.get(SalaryPeriod,calc.salary_period_id)
    if corruption=='employee':
        worker=make_user('employee');other=Employee(business_owner_id=s.owner.id,employee_user_id=worker.id)
        db.add(other);db.flush();period.employee_id=other.id
    elif corruption=='business':
        business=Business(name='Other business',business_type='salon');db.add(business);db.flush();period.business_id=business.id
    elif corruption=='dates':period.period_end=END+timedelta(days=1)
    elif corruption=='base':period.base_salary_exact=101
    else:
        visit(db,s,price=10);calc=calculate(db,s,'CZK');period=db.get(SalaryPeriod,calc.salary_period_id);period.commission_percentage=20
    db.commit()
    with pytest.raises(PayrollError,match='payroll_snapshot_review'):
        payroll.create_salary_payment(db,calc.id,payment(),s.owner.id)
    assert db.query(SalaryPayment).count()==0


def test_quote_is_one_statement_snapshot_during_concurrent_reprice(db,payroll_setup):
    from sqlalchemy import event
    from app.db.session import engine
    s=payroll_setup;row=visit(db,s,price=10);row_id=row.id;changed=[]
    def reprice_after_quote(conn,cursor,statement,parameters,context,executemany):
        if changed or not statement.lstrip().upper().startswith('SELECT') or 'FROM visits' not in statement:
            return
        changed.append(True)
        with engine.begin() as other:
            other.execute(text("UPDATE visits SET currency_snapshot='EUR',price=200 WHERE id=:id"),{'id':row_id})
            other.execute(text('UPDATE visit_items SET price=200 WHERE visit_id=:id'),{'id':row_id})
    event.listen(engine,'after_cursor_execute',reprice_after_quote)
    try:
        calc=calculate(db,s,'CZK')
    finally:
        event.remove(engine,'after_cursor_execute',reprice_after_quote)
    assert changed==[True]
    assert calc.currency=='CZK' and calc.total_revenue_exact==10 and calc.total_salary_exact==101
    eur=calculate(db,s,'EUR')
    assert eur.total_revenue_exact==200 and eur.total_salary_exact==20


def test_empty_period_uses_the_proven_unit_of_zero_salary_terms(db, payroll_setup):
    s = payroll_setup
    s.employee.base_salary = 0
    s.employee.base_salary_exact = Decimal('0.00')
    s.employee.base_salary_currency = 'CZK'
    db.commit()
    calc = calculate(db, s)
    assert calc.currency == 'CZK'
    assert calc.total_revenue_exact == calc.base_salary_exact == calc.total_salary_exact == Decimal('0.00')
    assert calc.salary_period.base_salary_currency == 'CZK'
