"""Own linked payroll expenses stay immutable through the ordinary expense routes."""
from decimal import Decimal
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.api.crud import crud_salary
from app.db.models.expense import Expense
from app.db.models.business import Business
from app.db.models.employee import Employee
from app.schemas.salary import SalaryPaymentCreate


@pytest.fixture
def payroll_setup(db, make_user):
    owner=make_user('business_owner')
    worker=make_user('employee')
    business=Business(name='Expense guard business', business_type='salon', currency='CZK')
    db.add(business);db.flush();owner.business_id=business.id
    employee=Employee(business_owner_id=owner.id, employee_user_id=worker.id,
        base_salary=100, base_salary_exact=Decimal('100.00'), base_salary_currency='CZK', commission_percentage=10)
    db.add(employee);db.commit()
    return SimpleNamespace(owner=owner,business=business,employee=employee)


def calculate(db, setup, unit):
    return crud_salary.calculate_salary(db,setup.employee.id,datetime(2026,1,1),datetime(2026,2,1),
        calculated_by_id=setup.owner.id,currency=unit)


def payment(value):
    return SalaryPaymentCreate(amount=value,payment_date=datetime(2026,1,2),status='completed')


MESSAGES = {
    'ru': 'Расход связан с завершённой выплатой зарплаты. Используйте отдельную финансовую корректировку.',
    'cz': 'Výdaj je spojen s dokončenou výplatou mzdy. Použijte samostatnou finanční opravu.',
    'en': 'This expense belongs to a completed salary payment. Use a separate financial correction.',
}


@pytest.mark.parametrize('locale', ['ru','cz','en'])
@pytest.mark.parametrize('method', ['put','delete'])
def test_own_linked_expense_mutations_return_localized_400_without_changing_evidence(
        db,payroll_setup,client,headers_for,locale,method):
    s=payroll_setup;calc=calculate(db,s,'CZK')
    paid=crud_salary.create_salary_payment(db,calc.id,payment(Decimal('20.01')),s.owner.id)
    expense=db.query(Expense).filter_by(salary_payment_id=paid.id).one()
    expense_id=expense.id
    def snapshot():
        return {table: [dict(r) for r in db.execute(text(f'SELECT * FROM {table} ORDER BY id')).mappings()]
                for table in ('salary_periods','salary_calculations','salary_payments','expenses')}
    before=snapshot();db.rollback()
    kwargs={'headers':{**headers_for(s.owner),'Accept-Language':locale}}
    if method=='put':kwargs['json']={'amount':'1.00','currency':'EUR','description':'should not change'}
    response=getattr(client,method)(f'/expenses/{expense_id}',**kwargs)
    assert response.status_code==400,response.text
    body=response.json()['error']
    assert body['detail']['reason']=='payroll_correction_required'
    assert body['message']==MESSAGES[locale]
    assert response.headers.get('cache-control')=='no-store'
    db.expire_all()
    assert snapshot()==before
    assert db.get(Expense,expense_id).currency=='CZK'
    assert db.get(Expense,expense_id).salary_payment_id==paid.id


def test_regular_own_expense_create_edit_delete_still_works(db,payroll_setup,client,headers_for):
    s=payroll_setup;headers=headers_for(s.owner)
    response=client.post('/expenses/',headers=headers,json={
        'business_id':s.business.id,'amount':12.34,'currency':'CZK','expense_date':'2026-01-02T00:00:00Z'})
    assert response.status_code==200,response.text
    assert response.headers.get('cache-control')=='no-store'
    expense_id=response.json()['id']
    response=client.put(f'/expenses/{expense_id}',headers=headers,json={'amount':15.67,'description':'Paper'})
    assert response.status_code==200,response.text
    assert response.json()['amount']==15.67 and response.json()['description']=='Paper'
    assert response.headers.get('cache-control')=='no-store'
    response=client.delete(f'/expenses/{expense_id}',headers=headers)
    assert response.status_code==200,response.text
    assert response.headers.get('cache-control')=='no-store'
    db.expire_all()
    assert db.get(Expense,expense_id) is None


@pytest.mark.parametrize('path,authenticated,status', [
    ('/expenses/',False,401),('/expenses/not-an-id',True,422),
    ('/expenses/999999',True,404),('/expenses/categories',True,200),
    ('/expenses/?date_from=bad-date',True,422),
])
def test_whole_financial_router_is_no_store(client,payroll_setup,headers_for,path,authenticated,status):
    headers=headers_for(payroll_setup.owner) if authenticated else {}
    response=client.get(path,headers=headers)
    assert response.status_code==status,response.text
    assert response.headers.get('cache-control')=='no-store'
