"""A salary child is addressed only through its actual employee and calculation."""
from datetime import datetime

import pytest
from sqlalchemy import select

from app.api.core.i18n import _
from app.db.models.salary_adjustment import SalaryAdjustment
from app.db.models.salary_calculation import SalaryCalculation
from app.db.models.salary_payment import SalaryPayment
from tests import test_staff_permissions as staff

salon=staff.salon


def ok(response):
    assert response.status_code==200,response.text
    return response.json()


@pytest.fixture
def world(client,salon):
    w=salon
    second=ok(client.post('/employees/create-with-user',headers=w['h'],json={
        'full_name':'Second payroll employee','email':'second.payroll@example.com','base_salary':1000}))
    period={'period_start':'2026-01-01T00:00:00Z','period_end':'2026-02-01T00:00:00Z'}
    first_salary=ok(client.post(f"/employees/{w['employee']['id']}/salary/calculate",headers=w['h'],json=period))
    second_salary=ok(client.post(f"/employees/{second['id']}/salary/calculate",headers=w['h'],json=period))
    root=f"/employees/{second['id']}/salary/{second_salary['id']}"
    adjustment=ok(client.post(root+'/adjustments',headers=w['h'],json={'type':'bonus','amount':10}))
    payment=ok(client.post(root+'/payments',headers=w['h'],json={
        'amount':25,'status':'pending','payment_date':datetime(2026,2,1).isoformat(),'payment_method':'other'}))
    w.update(second=second,first_salary=first_salary,second_salary=second_salary,adjustment=adjustment,payment=payment)
    return w


def state(db):
    return [[dict(row) for row in db.execute(select(model.__table__).order_by(model.id)).mappings()]
            for model in (SalaryCalculation,SalaryAdjustment,SalaryPayment)]


@pytest.mark.parametrize('action',['update_payment','delete_payment','delete_adjustment'])
def test_parent_employee_must_match_the_requested_salary_before_mutation(client,db,world,action):
    w=world;before=state(db);db.rollback()
    root=f"/employees/{w['employee']['id']}/salary/{w['second_salary']['id']}"
    for language in ('en','ru','cz'):
        headers={**w['h'],'Accept-Language':language}
        if action=='update_payment':
            response=client.put(root+f"/payments/{w['payment']['id']}",headers=headers,json={'notes':'Not this employee record'})
        elif action=='delete_payment':response=client.delete(root+f"/payments/{w['payment']['id']}",headers=headers)
        else:response=client.delete(root+f"/adjustments/{w['adjustment']['id']}",headers=headers)
        assert response.status_code==404,response.text
        assert response.json()['error']['message']==_('salary_calculation_not_found',language)
        assert response.headers['cache-control']=='no-store'
        assert state(db)==before;db.rollback()


def test_a_child_from_another_calculation_is_not_accepted(client,db,world):
    w=world;before=state(db);db.rollback()
    root=f"/employees/{w['employee']['id']}/salary/{w['first_salary']['id']}"
    response=client.put(root+f"/payments/{w['payment']['id']}",headers=w['h'],json={'notes':'Wrong calculation'})
    assert response.status_code==404,response.text
    assert state(db)==before


def test_actual_parent_paths_keep_working_and_are_private(client,db,world):
    w=world;root=f"/employees/{w['second']['id']}/salary/{w['second_salary']['id']}"
    response=client.put(root+f"/payments/{w['payment']['id']}",headers=w['h'],json={'notes':'Correct employee record'})
    assert ok(response)['notes']=='Correct employee record'
    assert response.headers['cache-control']=='no-store'
    assert client.delete(root+f"/adjustments/{w['adjustment']['id']}",headers=w['h']).status_code==200
    assert client.delete(root+f"/payments/{w['payment']['id']}",headers=w['h']).status_code==200
    db.expire_all();salary=db.get(SalaryCalculation,w['second_salary']['id'])
    assert salary.total_with_adjustments==1000 and salary.paid_amount==0
