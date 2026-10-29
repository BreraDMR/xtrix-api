"""Quoted service prices stay separate across currencies, including exports and caches."""
import csv
import io
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
import json
import math

import pytest
from sqlalchemy import event, update

from app.api.core.analytics import get_financial_report
from app.api.v1.endpoints import analytics as endpoints
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.user import User
from app.db.models.visit import Visit
from app.schemas.analytics import FinancialReportResponse
from tests import test_visit_financial_privacy as privacy

salon=privacy.salon


@pytest.fixture
def world(db,salon):
    salon['index']=0
    db.get(Business,salon['business']['id']).currency='USD';db.commit()
    return salon


def quote(db,w,price,unit='EUR',status='completed',start=None,deleted=False,client_id=None,staff_id=None,business_id=None):
    w['index']+=1
    start=start or datetime(2026,10,27)+timedelta(days=w['index'])
    valid_item=price is not None and math.isfinite(price) and abs(price)<99999999
    row=Visit(client_id=client_id or w['customer']['id'],specialist_id=staff_id or w['staff'].id,
        business_id=business_id or w['business']['id'],price=price if valid_item else 1,
        currency_snapshot=unit,status=status,payment_method='cash',date_start=start,
        date_end=start+timedelta(minutes=30),deleted_at=datetime(2026,10,29) if deleted else None)
    db.add(row);db.commit()
    if not valid_item:
        db.execute(update(Visit).where(Visit.id==row.id).values(price=price));db.commit()
    return row


def report(db,w,**kwargs):return get_financial_report(db,business_id=w['business']['id'],**kwargs)
def units(rows):return {row['currency']:row for row in rows}


def test_mixed_and_unknown_units_have_no_invented_grand_total(db,world):
    quote(db,world,1000,'CZK');quote(db,world,40,'EUR');quote(db,world,10,None)
    value=report(db,world)
    assert value['total_revenue'] is None and value['average_revenue_per_visit'] is None
    assert value['currency'] is None and value['value_basis']=='quoted_service_price'
    data=units(value['by_currency'])
    assert data['CZK']['amount']==Decimal('1000.00') and data['CZK']['count']==1
    assert data['EUR']['amount']==Decimal('40.00') and data['EUR']['count']==1
    assert data[None]['amount'] is None and data[None]['average'] is None and data[None]['count']==1
    assert data[None]['requires_review'] and 'USD' not in data
    assert value['top_clients']==[]
    assert value['revenue_by_status']['completed']['revenue'] is None
    assert value['revenue_by_period'][0]['revenue'] is None
    FinancialReportResponse.model_validate(value)


def test_invalid_quotes_taint_their_known_slice_instead_of_becoming_zero(db,world):
    for amount in [20,float('nan'),float('inf'),-1.50,0.001,None]:quote(db,world,amount)
    value=report(db,world);data=units(value['by_currency'])
    assert data['EUR']['amount'] is None and data['EUR']['average'] is None
    assert data['EUR']['count']==6 and data['EUR']['requires_review']
    assert value['total_revenue'] is None and value['average_revenue_per_visit'] is None


def test_single_known_currency_is_exact_even_under_a_small_decimal_context(db,world):
    quote(db,world,1000.01,'CZK');quote(db,world,0.01,'CZK')
    with localcontext() as context:
        context.prec=2
        value=report(db,world)
    assert value['currency']=='CZK' and value['total_revenue']==pytest.approx(1000.02)
    assert value['by_currency'][0]['amount']==Decimal('1000.02')
    assert value['by_currency'][0]['average']==Decimal('500.01')
    payload=FinancialReportResponse.model_validate(value).model_dump(mode='json')
    assert payload['by_currency'][0]['amount']=='1000.02'


@pytest.mark.parametrize('grouping,expected',[
    ('day',['2026-12-31','2027-01-01']),('week',['2026-12-28']),('month',['2026-12','2027-01']),('year',['2026','2027']),
])
def test_all_advertised_periods_group_without_losing_quote_rows(db,world,grouping,expected):
    quote(db,world,10,start=datetime(2026,12,31,12));quote(db,world,20,start=datetime(2027,1,1,12))
    value=report(db,world,group_by=grouping)
    assert [row['period'] for row in value['revenue_by_period']]==expected
    assert sum(row['count'] for row in value['revenue_by_period'])==2
    assert all(row['by_currency'][0]['currency']=='EUR' for row in value['revenue_by_period'])


def test_deleted_foreign_staff_and_outside_date_quotes_do_not_enter_scope(db,world):
    w=world;date=datetime(2026,10,27,12)
    quote(db,w,10,start=date);quote(db,w,999,start=date+timedelta(days=1),deleted=True)
    quote(db,w,777,start=date+timedelta(days=5))
    other_staff=User(full_name='Other scoped staff',email='other-financial-staff@example.test',hashed_password='synthetic',
        role='specialist',business_id=w['business']['id']);db.add(other_staff);db.commit()
    quote(db,w,555,start=date+timedelta(days=1),staff_id=other_staff.id)
    other=Business(name='Foreign finance',business_type='salon');db.add(other);db.commit()
    quote(db,w,888,start=date+timedelta(days=2),business_id=other.id)
    value=report(db,w,specialist_id=w['staff'].id,date_from=date,date_to=date+timedelta(days=2))
    assert value['total_revenue']==10 and value['by_currency'][0]['count']==1


def test_clients_are_ranked_only_within_the_same_known_currency(db,world):
    second=Client(full_name='Second currency client');db.add(second);db.commit()
    quote(db,world,1000,'CZK');quote(db,world,5,'EUR')
    quote(db,world,10,'CZK',client_id=second.id);quote(db,world,40,'EUR',client_id=second.id)
    value=report(db,world);groups=units(value['top_clients_by_currency'])
    assert value['top_clients']==[]
    assert groups['CZK']['clients'][0]['client_id']==world['customer']['id']
    assert groups['EUR']['clients'][0]['client_id']==second.id
    assert groups['EUR']['clients'][0]['amount']==Decimal('40.00')


@pytest.mark.parametrize('format',['csv','excel'])
def test_financial_exports_have_currency_rows_and_unknown_amount_is_blank(client,db,world,format):
    quote(db,world,1000,'CZK');quote(db,world,40,'EUR');quote(db,world,10,None)
    response=client.post('/analytics/reports/export',headers=world['oh'],json={'report_type':'financial','format':format})
    assert response.status_code==200,response.text
    assert response.headers['cache-control']=='no-store'
    if format=='csv':
        rows=list(csv.DictReader(io.StringIO(response.text)))
    else:
        from openpyxl import load_workbook
        book=load_workbook(io.BytesIO(response.content),read_only=True)
        values=list(book.active.values);rows=[dict(zip(values[0],row)) for row in values[1:]];book.close()
    totals={row['Currency']:row for row in rows if row['Period']=='TOTAL'}
    assert totals['CZK']['Quoted Service Value']=='1000.00'
    assert totals['EUR']['Quoted Service Value']=='40.00'
    assert totals['UNKNOWN']['Quoted Service Value'] in ['',None]
    assert totals['UNKNOWN']['Visit Count'] in ['1',1]
    assert all(row['Value Basis']=='quoted_service_price' for row in rows)


def test_versioned_cache_never_uses_old_mixed_totals_and_serializes_exact_slices(client,db,world,monkeypatch):
    quote(db,world,1000.01,'CZK');calls=[];writes=[]
    old={'total_revenue':1040,'average_revenue_per_visit':520,'revenue_by_status':{},'revenue_by_period':[],'top_clients':[]}
    def read(kind,**kwargs):
        calls.append(kind);return old if kind=='financial_report' else None
    def write(kind,value,**kwargs):
        json.dumps(value)
        writes.append((kind,value))
    monkeypatch.setattr(endpoints,'get_cached_analytics',read);monkeypatch.setattr(endpoints,'cache_analytics',write)
    response=client.get('/analytics/financial/report',headers=world['oh'])
    assert response.status_code==200,response.text
    assert calls==['financial_report_v2'] and writes[0][0]=='financial_report_v2'
    assert writes[0][1]['by_currency'][0]['amount']=='1000.01'
    assert response.json()['total_revenue']==pytest.approx(1000.01)


def test_core_uses_one_grouped_statement(db,world):
    second=Client(full_name='Grouped query client');db.add(second);db.commit()
    for index in range(20):quote(db,world,index+0.01,'CZK' if index%2 else 'EUR',client_id=second.id if index%3 else None)
    statements=[]
    def before(conn,cursor,sql,*args):
        if 'FROM visits' in sql:statements.append(sql)
    event.listen(db.get_bind(),'before_cursor_execute',before)
    try:report(db,world)
    finally:event.remove(db.get_bind(),'before_cursor_execute',before)
    assert len(statements)==1, f'Expected one snapshot statement, got {len(statements)}'
    assert all('GROUP BY' in statement for statement in statements)


def test_empty_report_keeps_zero_without_claiming_a_currency(db,world):
    value=report(db,world)
    assert value['total_revenue']==0 and value['currency'] is None and value['by_currency']==[]
    assert value['value_basis']=='quoted_service_price' and value['revenue_by_period']==[]


def test_quote_changes_after_the_first_select_cannot_split_the_report_snapshot(db,world):
    from sqlalchemy.orm import Session
    row=quote(db,world,10,'EUR',status='planned');visit_id=row.id;changed=[]
    def after(conn,cursor,sql,*args):
        if 'FROM visits' in sql and not changed:
            changed.append(True)
            with Session(db.get_bind()) as writer:
                writer.execute(update(Visit).where(Visit.id==visit_id).values(price=20,currency_snapshot='CZK'))
                writer.commit()
    event.listen(db.get_bind(),'after_cursor_execute',after)
    try:value=report(db,world)
    finally:event.remove(db.get_bind(),'after_cursor_execute',after)
    assert changed
    assert value['by_currency'][0]['currency']=='EUR' and value['by_currency'][0]['amount']==Decimal('10.00')
    assert value['revenue_by_status']['planned']['by_currency'][0]['currency']=='EUR'
    assert value['revenue_by_status']['planned']['by_currency'][0]['amount']==Decimal('10.00')
    assert value['revenue_by_period'][0]['by_currency'][0]['currency']=='EUR'
    assert value['top_clients_by_currency'][0]['currency']=='EUR'
    fresh=report(db,world)
    assert fresh['by_currency'][0]['currency']=='CZK' and fresh['by_currency'][0]['amount']==Decimal('20.00')
