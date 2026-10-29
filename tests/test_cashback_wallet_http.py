"""Private balance responses label units and preserve unknown legacy evidence."""
from decimal import Decimal

import pytest

from app.db.models.client_business_relation import ClientBusinessRelation
from app.db.models.client_cashback_balance import ClientCashbackBalance
from app.db.models.loyalty_program import LoyaltyTransaction
from app.db.models.user import User
from tests import test_loyalty_visit_quote_lock as quote


@pytest.fixture
def world(db,monkeypatch):
    return quote.world.__wrapped__(db,monkeypatch)


def test_balance_lists_exact_known_units_and_readonly_unknown_history(client,db,world,headers_for):
    relation=db.query(ClientBusinessRelation).one();relation.loyalty_cashback=123.456789
    db.add(ClientCashbackBalance(client_id=world['client'],business_id=world['business'],currency='CZK',balance=Decimal('1000.01')))
    db.commit()
    response=client.get(f"/loyalty/balance/{world['client']}",headers=headers_for(db.get(User,world['staff'])))
    assert response.status_code==200,response.text
    body=response.json()
    assert body['cashback'] is None and body['cashback_currency'] is None
    assert body['cashback_balances']==[
        {'currency':'CZK','amount':'1000.01','requires_review':False},
        {'currency':'EUR','amount':'100.00','requires_review':False},
        {'currency':None,'amount':'123.456789','requires_review':True}]


def test_single_known_balance_labels_the_compatibility_scalar(client,db,world,headers_for):
    response=client.get(f"/loyalty/balance/{world['client']}",headers=headers_for(db.get(User,world['staff'])))
    assert response.status_code==200,response.text
    assert response.json()['cashback']==100 and response.json()['cashback_currency']=='EUR'
    assert response.json()['cashback_balances'][0]['amount']=='100.00'


def test_spending_and_transaction_history_expose_exact_currency_snapshots(client,db,world,headers_for):
    headers=headers_for(db.get(User,world['staff']))
    response=client.post(f"/loyalty/use-points?client_id={world['client']}",headers=headers,
        json={'visit_id':world['visit'],'cashback_to_use':'10.01'})
    assert response.status_code==200,response.text
    response=client.get(f"/loyalty/transactions/{world['client']}",headers=headers)
    assert response.status_code==200,response.text
    tx=response.json()['items'][0]
    assert tx['cashback_currency']=='EUR'
    assert tx['cashback_amount_exact']=='-10.01' and tx['cashback_balance_after_exact']=='89.99'
    assert tx['cashback_requires_review'] is False


@pytest.mark.parametrize('locale,expected',[
    ('ru','Выберите визит'),('en','Choose a visit'),('cz','Vyberte návštěvu')])
def test_missing_visit_returns_a_human_explanation(client,db,world,headers_for,locale,expected):
    headers=headers_for(db.get(User,world['staff']));headers['Accept-Language']=locale
    response=client.post(f"/loyalty/use-points?client_id={world['client']}",headers=headers,
        json={'cashback_to_use':1})
    assert response.status_code==400,response.text
    assert expected in response.json()['error']['message']
    assert db.query(LoyaltyTransaction).count()==0


def test_portal_shows_own_exact_wallets_and_never_another_client_balance(client,db,world):
    from app.api.crud import crud_client_portal
    from app.db.models.business import Business
    business=db.get(Business,world['business']);business.currency='CZK'
    db.query(ClientBusinessRelation).one().loyalty_cashback=77
    db.add(ClientBusinessRelation(client_id=world['other_client'],business_id=world['business'],loyalty_cashback=9999))
    db.add(ClientCashbackBalance(client_id=world['other_client'],business_id=world['business'],currency='CZK',balance=9999))
    _,raw=crud_client_portal.create_session(db,world['business'],world['client']);db.commit()
    response=client.get('/public/client-portal/loyalty',headers={'X-Portal-Token':raw})
    assert response.status_code==200,response.text
    body=response.json()
    assert body['cashback'] is None and body['cashback_currency'] is None
    assert body['cashback_balances']==[
        {'currency':'EUR','amount':'100.00','requires_review':False},
        {'currency':None,'amount':'77.0','requires_review':True}]
    assert '9999' not in response.text
    assert client.get('/public/client-portal/loyalty').status_code==401


@pytest.mark.parametrize('amount',['NaN','Infinity','1.001',-1,True])
def test_invalid_cashback_never_writes_a_ledger_entry(client,db,world,headers_for,amount):
    response=client.post(f"/loyalty/use-points?client_id={world['client']}",
        headers=headers_for(db.get(User,world['staff'])),json={'visit_id':world['visit'],'cashback_to_use':amount})
    assert response.status_code==422,response.text
    assert db.query(LoyaltyTransaction).count()==0


def test_financial_wallet_data_stays_scoped_to_the_authorized_client(client,db,world,headers_for):
    headers=headers_for(db.get(User,world['staff']))
    assert client.get(f"/loyalty/balance/{world['other_client']}",headers=headers).status_code==404
    assert client.get(f"/loyalty/transactions/{world['other_client']}",headers=headers).status_code==404
    assert client.get(f"/loyalty/balance/{world['client']}").status_code==401


@pytest.mark.parametrize('path', ['balance','transactions','statistics','spend'])
def test_staff_without_financial_rights_cannot_read_or_spend_wallets(client,db,world,headers_for,make_user,path):
    from app.db.models.employee import Employee
    employee=make_user('employee',business_id=world['business'],employer_id=world['staff'])
    db.add(Employee(business_owner_id=world['staff'],employee_user_id=employee.id,
        permissions={'can_view_financials':False},is_active=True));db.commit()
    headers=headers_for(employee)
    if path=='spend':
        response=client.post(f"/loyalty/use-points?client_id={world['client']}",headers=headers,
            json={'visit_id':world['visit'],'cashback_to_use':1})
    else:
        url=f"/loyalty/{path}/{world['client']}" if path!='statistics' else '/loyalty/analytics/statistics'
        response=client.get(url,headers=headers)
    assert response.status_code==403,response.text
    assert response.headers['cache-control']=='no-store'
    assert db.query(LoyaltyTransaction).count()==0
