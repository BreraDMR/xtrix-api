from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from math import isfinite

import pytest
from sqlalchemy import event, update

from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.expense import Expense
from app.db.models.expense_category import ExpenseCategory
from app.db.models.visit import Visit

START = datetime(2026, 1, 1)
END = datetime(2026, 2, 1)
PARAMS = {'date_from': START.isoformat(), 'date_to': END.isoformat()}


@pytest.fixture
def world(db, make_user, headers_for):
    owner = make_user('business_owner')
    business = Business(name='Reports', business_type='salon', currency='EUR')
    other = Business(name='Other', business_type='salon', currency='CZK')
    db.add_all([business, other]); db.flush()
    owner.business_id = business.id
    customer = Client(full_name='Reports client', owner_id=owner.id)
    category = ExpenseCategory(name='Rent', business_id=business.id)
    db.add_all([customer, category]); db.commit()
    return dict(owner=owner, business=business, other=other, customer=customer,
                category=category, headers=headers_for(owner))


def quote(db, w, unit='CZK', amount=100, **extra):
    row = Visit(business_id=w['business'].id, specialist_id=w['owner'].id,
                client_id=w['customer'].id, date_start=START+timedelta(days=1, hours=2*db.query(Visit).count()),
                status='completed', price=amount if amount is None or isfinite(amount) else 0, currency_snapshot=unit, **extra)
    db.add(row); db.commit()
    if amount is not None and not isfinite(amount):
        db.execute(update(Visit).where(Visit.id==row.id).values(price=amount));db.commit()
    return row


def expense(db, w, unit='CZK', amount=20, categorized=True, **extra):
    row = Expense(business_id=w['business'].id, category_id=w['category'].id if categorized else None,
                  amount=amount, currency=unit, expense_date=START+timedelta(days=1), **extra)
    db.add(row); db.commit()
    return row


def report(client, w):
    response = client.get('/expenses/reports/profit', headers=w['headers'], params=PARAMS)
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    return response.json()


def test_mixed_units_have_no_scalar_profit_or_daily_sum(client, db, world):
    w=world
    quote(db,w,'CZK',100);quote(db,w,'EUR',40)
    expense(db,w,'CZK',20);expense(db,w,'EUR',5)
    data=report(client,w)
    assert data['profit'] is None, data
    assert data['revenue'] is None and data['expenses'] is None and data['profit_margin'] is None
    assert data['currency'] is None and data['requires_review']
    assert data['value_basis']=='quoted_service_price'
    units={row['currency']:row for row in data['by_currency']}
    assert units['CZK']['profit']=='80.00' and units['EUR']['profit']=='35.00'
    assert data['daily'][0]['profit'] is None
    assert data['expenses_by_category'][0]['total'] is None
    assert {r['currency']:r['total'] for r in data['expenses_by_category'][0]['by_currency']}=={'CZK':'20.00','EUR':'5.00'}


def test_single_unit_exact_gross_quote_and_deleted_exclusion(client, db, world):
    w=world
    quote(db,w,'CZK',1000.01);quote(db,w,'CZK',0.01)
    quote(db,w,'EUR',500,deleted_at=START)
    expense(db,w,'CZK',20.01)
    with localcontext() as context:
        context.prec=2
        data=report(client,w)
    assert data['currency']=='CZK'
    assert data['revenue']==1000.02 and data['profit']==980.01
    assert data['by_currency'][0]['revenue']=='1000.02'
    assert data['by_currency'][0]['profit']=='980.01'
    assert data['by_currency'][0]['quote_count']==2
    assert not data['requires_review']


@pytest.mark.parametrize('kind,unit,value', [('quote',None,100),('quote','CZK',1.001),('quote','CZK',float('inf')),
                                             ('quote','CZK',float('nan')),('expense','ZZZ',30),
                                             ('expense','CZK',1.001),('expense','CZK',float('inf'))])
def test_unknown_and_invalid_values_require_review_without_guessing(client,db,world,kind,unit,value):
    w=world
    if kind=='quote':quote(db,w,unit,value)
    else:expense(db,w,unit,value)
    data=report(client,w)
    assert data['profit'] is None and data['currency'] is None,data
    assert data['requires_review']
    row=data['by_currency'][0]
    assert row['requires_review'] and row['profit'] is None
    assert row['currency']==(unit if unit=='CZK' else None)
    if kind=='quote':assert row['revenue'] is None
    else:assert row['expenses'] is None


def test_uncategorized_expenses_are_visible_and_foreign_business_excluded(client,db,world):
    w=world
    expense(db,w,'CZK',15,categorized=False)
    foreign=Expense(business_id=w['other'].id,category_id=w['category'].id,amount=999,currency='EUR',expense_date=START)
    db.add(foreign);db.commit()
    data=report(client,w)
    assert data['expenses']==15 and data['currency']=='CZK'
    assert data['expenses_by_category'][0]['category_id'] is None
    response=client.get('/expenses/reports/expenses-by-category',headers=w['headers'],params=PARAMS)
    assert response.status_code==200,response.text
    assert len(response.json())==1 and response.json()[0]['total']==15


def test_financial_projection_uses_one_statement_snapshot(client,db,world):
    w=world;quote(db,w);expense(db,w)
    statements=[]
    def capture(conn,cursor,statement,params,context,many):
        sql=statement.lower()
        if sql.lstrip().startswith(('select','with')) and ('sum(' in sql or 'stored_profit_rows' in sql):statements.append(statement)
    event.listen(db.get_bind(),'before_cursor_execute',capture)
    try:report(client,w)
    finally:event.remove(db.get_bind(),'before_cursor_execute',capture)
    assert len(statements)==1,statements


def test_empty_report_keeps_zero_without_inventing_unit(client,world):
    data=report(client,world)
    assert data['revenue']==0 and data['expenses']==0 and data['profit']==0
    assert data['currency'] is None and not data['requires_review'] and data['by_currency']==[]


def test_report_is_coherent_when_another_transaction_commits(client, db, world):
    from sqlalchemy import update
    from sqlalchemy.orm import Session

    w=world
    visit=quote(db,w,'CZK',100)
    spent=expense(db,w,'CZK',20)
    changed=[]

    def mutate_after_first_projection(conn,cursor,statement,params,context,many):
        sql=statement.lower()
        if changed or not sql.lstrip().startswith(('select','with')) or not ('sum(' in sql or 'stored_profit_rows' in sql):
            return
        changed.append(True)
        with Session(db.get_bind()) as writer:
            writer.execute(update(Visit).where(Visit.id==visit.id).values(price=200,currency_snapshot='EUR'))
            writer.execute(update(Expense).where(Expense.id==spent.id).values(amount=5,currency='EUR'))
            writer.commit()

    event.listen(db.get_bind(),'after_cursor_execute',mutate_after_first_projection)
    try:data=report(client,w)
    finally:event.remove(db.get_bind(),'after_cursor_execute',mutate_after_first_projection)
    assert changed
    assert data['revenue']==100 and data['expenses']==20 and data['profit']==80,data
    assert data['daily'][0]['revenue']==100 and data['daily'][0]['expenses']==20
    assert data['expenses_by_category'][0]['total']==20
    db.expire_all()
    assert db.get(Visit,visit.id).currency_snapshot=='EUR'
    assert db.get(Expense,spent.id).amount==5


@pytest.mark.parametrize('kind', ['quote', 'expense'])
def test_float_cast_does_not_erase_subcent_legacy_digits(client, db, world, kind):
    if kind=='quote':quote(db,world,'CZK',1.0000000000000002)
    else:expense(db,world,'CZK',1.0000000000000002)
    data=report(client,world)
    assert data['requires_review'] and data['profit'] is None,data


@pytest.mark.parametrize('month,next_month', [(datetime(2026, 4, 1), datetime(2026, 5, 1)),
                                              (datetime(2026, 12, 1), datetime(2027, 1, 1))])
def test_default_month_includes_final_microseconds_and_excludes_next_month(client, db, world, monkeypatch, make_user, month, next_month):
    from app.api.v1.endpoints import expenses as endpoint

    monkeypatch.setattr(endpoint, 'utcnow', lambda: month + timedelta(days=12, hours=9))
    w=world
    last=next_month-timedelta(microseconds=1)
    visit=quote(db,w,'CZK',100.01)
    spent=expense(db,w,'CZK',20.01)
    db.execute(update(Visit).where(Visit.id==visit.id).values(date_start=last, date_end=last+timedelta(minutes=30)))
    db.execute(update(Expense).where(Expense.id==spent.id).values(expense_date=last))
    db.commit()
    # Different specialists avoid an overlapping slot while retaining a business quote.
    next_worker=make_user('specialist', business_id=w['business'].id)
    next_quote=Visit(business_id=w['business'].id, specialist_id=next_worker.id, client_id=w['customer'].id,
                     date_start=next_month, status='completed', price=900, currency_snapshot='CZK')
    db.add(next_quote)
    db.add(Expense(business_id=w['business'].id, amount=800, currency='CZK', expense_date=next_month))
    db.commit()
    response=client.get('/expenses/reports/profit',headers=w['headers'])
    assert response.status_code==200,response.text
    data=response.json()
    assert data['revenue']==100.01 and data['expenses']==20.01 and data['profit']==80,data
    assert data['period']['to']==last.isoformat()+'Z'
    assert data['daily'][0]['date']==last.date().isoformat()
    assert data['daily'][0]['by_currency'][0]['revenue']=='100.01'
    assert data['daily'][0]['by_currency'][0]['expenses']=='20.01'
    assert len(data['daily'])==1
