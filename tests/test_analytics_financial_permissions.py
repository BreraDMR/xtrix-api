"""Analytics and exports obey financial permissions before reading any money."""
import pytest
from sqlalchemy import update

from app.api.core.i18n import _
from app.api.v1.endpoints import analytics as endpoints
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.user import User
from app.db.models.visit import Visit
from tests import test_visit_financial_privacy as visits

salon=visits.salon


@pytest.fixture
def world(client,db,salon):
    created=visits.ok(client.post('/visits/',headers=salon['oh'],json={**salon['body'],'status':'completed'}))
    salon['visit_id']=created['id']
    return salon


def private(response,status):
    assert response.status_code==status,response.text
    assert response.headers.get('cache-control')=='no-store'


@pytest.mark.parametrize('path',[
    '/analytics/financial/report', '/analytics/services/popular', '/analytics/clients/statistics',
    '/analytics/payments/statistics', '/analytics/trends?metric=revenue', '/analytics/trends?metric=average_check',
])
def test_denied_financial_routes_are_localized_and_private(client,world,path):
    for language in ['en','ru','cz']:
        response=client.get(path,headers={**world['eh'],'Accept-Language':language})
        private(response,403)
        assert response.json()['error']['message']==_('not_allowed',language)
        assert '123.45' not in response.text


@pytest.mark.parametrize('kind',['financial','services','clients'])
def test_csv_and_excel_cannot_bypass_financial_permissions(client,world,kind):
    for format in ['csv','excel']:
        for language in ['en','ru','cz']:
            response=client.post('/analytics/reports/export',headers={**world['eh'],'Accept-Language':language},
                json={'report_type':kind,'format':format})
            private(response,403)
            assert response.json()['error']['message']==_('not_allowed',language)


def test_denied_financial_requests_never_reach_queries_cache_or_exporters(client,world,monkeypatch):
    reached=[]
    def forbidden(*args,**kwargs):
        reached.append(True)
        raise AssertionError('Denied financial path reached a reader or exporter')
    for name in ['get_cached_analytics','cache_analytics','get_financial_report','get_popular_services',
                 'get_client_statistics','get_payment_statistics','get_trends_analysis','export_to_csv','export_to_excel']:
        monkeypatch.setattr(endpoints,name,forbidden)
    for path in ['/analytics/financial/report','/analytics/services/popular','/analytics/clients/statistics',
                 '/analytics/payments/statistics','/analytics/trends?metric=revenue','/analytics/trends?metric=average_check']:
        private(client.get(path,headers=world['eh']),403)
    for kind in ['financial','services','clients']:
        private(client.post('/analytics/reports/export',headers=world['eh'],json={'report_type':kind,'format':'csv'}),403)
    assert not reached


def test_nonmonetary_staff_paths_and_visit_exports_remain_available(client,world):
    for path in ['/analytics/visits/statistics','/analytics/retention/metrics',
                 '/analytics/trends?metric=visits','/analytics/trends?metric=clients','/analytics/trends?metric=load']:
        response=client.get(path,headers=world['eh']);private(response,200)
        assert '123.45' not in response.text
    for format in ['csv','excel']:
        response=client.post('/analytics/reports/export',headers=world['eh'],json={'report_type':'visits','format':format})
        private(response,200)
        assert response.headers['content-disposition'].endswith('.'+('csv' if format=='csv' else 'xlsx')+'"')


def test_owner_and_financially_granted_staff_can_read_money_without_tenant_leaks(client,db,world):
    other_business=Business(name='Foreign analytics salon',business_type='salon')
    other_client=Client(full_name='Foreign client marker')
    db.add_all([other_business,other_client]);db.flush()
    other_staff=User(full_name='Foreign staff',email='foreign.analytics@example.test',hashed_password='synthetic',
                     role='business_owner',business_id=other_business.id)
    db.add(other_staff);db.flush()
    from datetime import datetime,timedelta
    db.add(Visit(client_id=other_client.id,business_id=other_business.id,specialist_id=other_staff.id,
        date_start=datetime(2026,1,1),date_end=datetime(2026,1,1)+timedelta(hours=1),price=98765.43,
        status='completed',payment_method='cash'));db.commit()
    visits.ok(client.put(f"/employees/{world['employee']['id']}",headers=world['oh'],
        json={'permissions':{'can_create_visits':True,'can_view_financials':True}}))
    for headers in [world['oh'],world['eh']]:
        response=client.get('/analytics/financial/report',headers=headers);private(response,200)
        assert response.json()['total_revenue']==pytest.approx(123.45)
        assert '98765.43' not in response.text and 'Foreign client marker' not in response.text
        for kind in ['financial','services','clients']:
            for format in ['csv','excel']:
                private(client.post('/analytics/reports/export',headers=headers,json={'report_type':kind,'format':format}),200)
    response=client.get('/analytics/financial/report',headers=world['oh'],params={'specialist_id':other_staff.id})
    private(response,403)


def test_owner_advanced_money_paths_obey_the_same_guard_before_readers(client,world,monkeypatch):
    from app.api.core import permissions
    monkeypatch.setattr(permissions,'can_view_financials',lambda *args:False)
    def forbidden(*args,**kwargs):raise AssertionError('Denied advanced report reached its reader')
    for name in ['get_forecast','get_service_and_specialist_analytics','get_smart_insights','get_anonymous_benchmarks']:
        monkeypatch.setattr(endpoints,name,forbidden)
    for path in ['/analytics/forecast?forecast_type=revenue','/analytics/services-specialists','/analytics/insights','/analytics/benchmarks']:
        private(client.get(path,headers=world['oh']),403)


def test_no_store_covers_auth_validation_and_global_error_responses(client,world,monkeypatch):
    response=client.get('/analytics/visits/statistics')
    assert response.status_code in [401,403]
    assert response.headers.get('cache-control')=='no-store'
    private(client.get('/analytics/trends?metric=invalid',headers=world['oh']),422)
    from fastapi.testclient import TestClient
    from app.main import app
    def fail(*args,**kwargs):raise RuntimeError('Synthetic private analytics failure')
    monkeypatch.setattr(endpoints,'get_visit_statistics',fail)
    monkeypatch.setattr(endpoints,'get_cached_analytics',lambda *args,**kwargs:None)
    with TestClient(app,raise_server_exceptions=False) as transport:
        private(transport.get('/analytics/visits/statistics',headers=world['oh']),500)


def test_revoking_financial_permission_applies_before_a_possible_cache_hit(client,world,monkeypatch):
    visits.ok(client.put(f"/employees/{world['employee']['id']}",headers=world['oh'],
        json={'permissions':{'can_create_visits':True,'can_view_financials':True}}))
    private(client.get('/analytics/financial/report',headers=world['eh']),200)
    visits.ok(client.put(f"/employees/{world['employee']['id']}",headers=world['oh'],
        json={'permissions':{'can_create_visits':True,'can_view_financials':False}}))
    monkeypatch.setattr(endpoints,'get_cached_analytics',lambda *args,**kwargs:{'total_revenue':99999})
    private(client.get('/analytics/financial/report',headers=world['eh']),403)
