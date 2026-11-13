"""The rest of analytics never adds prices of different (or unknown) currencies together.

Services/specialists, smart insights, benchmarks, popular services, client
spending, payment stats and the services/clients exports. Same rule as the
financial report: one row per stored visit currency, old scalar fields only
when there's exactly one known currency.
"""
import csv
import io
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from openpyxl import load_workbook

from app.api.core import advanced_analytics
from app.api.core.advanced_analytics import (
    get_anonymous_benchmarks, get_service_and_specialist_analytics, get_smart_insights,
)
from app.api.core.analytics import get_client_statistics, get_payment_statistics, get_popular_services
from app.db.models.business import Business
from app.db.models.visit import Visit
from app.schemas.analytics import (
    AnonymousBenchmarksResponse, ClientStatisticsResponse, PaymentStatisticsResponse,
    PopularServicesResponse, ServiceAndSpecialistAnalyticsResponse,
)
from tests import test_financial_currency_report as currency_report

salon = currency_report.salon
world = currency_report.world
units = currency_report.units

NOW = datetime(2026, 11, 20, 12, 0)
CURRENT = datetime(2026, 11, 5, 9, 0)
PREVIOUS = datetime(2026, 10, 5, 9, 0)


@pytest.fixture(autouse=True)
def frozen_now(monkeypatch):
    monkeypatch.setattr(advanced_analytics, 'utcnow', lambda: NOW)


def visit(db, w, price, unit, start=None, status='completed', deleted=False, service=0,
          business_id=None, client_id=None, method='cash'):
    w['index'] += 1
    start = start or datetime(2026, 10, 1, 8) + timedelta(hours=w['index'])
    row = Visit(client_id=client_id or w['customer']['id'], specialist_id=w['staff'].id,
                business_id=business_id or w['business']['id'],
                service_id=w['services'][service]['id'] if service is not None else None,
                price=price, currency_snapshot=unit, status=status, payment_method=method,
                date_start=start, date_end=start + timedelta(minutes=30),
                deleted_at=datetime(2026, 10, 29) if deleted else None)
    db.add(row)
    db.commit()
    return row


def mixed(db, w):
    # same service, same specialist, two currencies + one visit with no currency
    visit(db, w, 1000, 'CZK')
    visit(db, w, 500, 'CZK', method='card')
    visit(db, w, 40, 'EUR')
    visit(db, w, 7, None)


def amounts(rows):
    return {row['currency']: row for row in rows}


# --- services and specialists ---------------------------------------------

def test_service_and_specialist_numbers_are_per_currency(db, world):
    mixed(db, world)
    value = get_service_and_specialist_analytics(db, business_id=world['business']['id'])
    ServiceAndSpecialistAnalyticsResponse.model_validate(value)
    [service] = value['services']
    assert service['visit_count'] == 4
    assert service['total_revenue'] is None and service['average_price'] is None
    assert service['ltv_per_client'] is None and service['currency'] is None
    data = amounts(service['by_currency'])
    assert data['CZK']['amount'] == Decimal('1500.00') and data['CZK']['average'] == Decimal('750.00')
    assert data['CZK']['count'] == 2 and data['CZK']['per_client'] == Decimal('1500.00')
    assert data['EUR']['amount'] == Decimal('40.00') and not data['EUR']['requires_review']
    assert data[None]['requires_review'] and data[None]['amount'] is None
    [specialist] = value['specialists']
    assert specialist['total_revenue'] is None and specialist['average_check'] is None
    assert specialist['revenue_per_client'] is None
    data = amounts(specialist['by_currency'])
    assert data['CZK']['amount'] == Decimal('1500.00') and data['EUR']['amount'] == Decimal('40.00')


def test_service_analytics_keep_old_fields_for_one_currency(db, world):
    visit(db, world, 1000, 'CZK')
    visit(db, world, 500.5, 'CZK')
    visit(db, world, 9000, 'EUR', deleted=True)
    value = get_service_and_specialist_analytics(db, business_id=world['business']['id'])
    [service] = value['services']
    assert service['currency'] == 'CZK' and service['visit_count'] == 2
    assert service['total_revenue'] == pytest.approx(1500.5)
    assert service['average_price'] == pytest.approx(750.25)
    assert service['ltv_per_client'] == pytest.approx(1500.5)
    [specialist] = value['specialists']
    assert specialist['total_revenue'] == pytest.approx(1500.5) and specialist['currency'] == 'CZK'


def test_one_service_in_one_currency_does_not_get_a_scalar_when_the_salon_has_two(db, world):
    # an old frontend formats every row with the salon currency, so no scalars at all
    visit(db, world, 1000, 'CZK', service=0)
    visit(db, world, 40, 'EUR', service=1)
    value = get_service_and_specialist_analytics(db, business_id=world['business']['id'])
    assert all(row['total_revenue'] is None for row in value['services'])
    assert {row['by_currency'][0]['currency'] for row in value['services']} == {'CZK', 'EUR'}
    assert all(row['currency'] for row in value['services'])


# --- smart insights --------------------------------------------------------

def test_insights_report_a_revenue_drop_per_currency(db, world):
    visit(db, world, 100, 'CZK', start=CURRENT)
    visit(db, world, 1000, 'CZK', start=PREVIOUS)
    visit(db, world, 90, 'EUR', start=CURRENT + timedelta(hours=1))
    visit(db, world, 30, 'EUR', start=PREVIOUS + timedelta(hours=1))
    insights = get_smart_insights(db, business_id=world['business']['id'], locale='en')
    revenue = [row for row in insights if row['title'] in {'Revenue Decline', 'Revenue Growth'}]
    assert len(revenue) == 2
    drop = next(row for row in revenue if row['title'] == 'Revenue Decline')
    grow = next(row for row in revenue if row['title'] == 'Revenue Growth')
    assert 'CZK' in drop['message'] and '90.0' in drop['message']
    assert 'EUR' in grow['message'] and '200.0' in grow['message']


def test_insights_for_one_currency_stay_as_before(db, world):
    visit(db, world, 100, 'CZK', start=CURRENT)
    visit(db, world, 1000, 'CZK', start=PREVIOUS)
    insights = get_smart_insights(db, business_id=world['business']['id'], locale='en')
    [drop] = [row for row in insights if row['title'] == 'Revenue Decline']
    assert drop['message'] == 'Revenue decreased by 90.0% compared to last month.'


# --- benchmarks between salons ---------------------------------------------

def other_salon(db, w, name, currency):
    business = Business(name=name, business_type='salon', currency=currency)
    db.add(business)
    db.commit()
    return business.id


def test_benchmarks_never_average_czk_with_eur(db, world):
    euro = other_salon(db, world, 'Euro Salon', 'EUR')
    visit(db, world, 1000, 'CZK')
    visit(db, world, 3000, 'CZK')
    visit(db, world, 40, 'EUR', business_id=euro)
    value = get_anonymous_benchmarks(db, min_businesses=1, locale='en')
    AnonymousBenchmarksResponse.model_validate(value)
    assert value['available'] is True
    assert value['average_check'] is None and value['currency'] is None
    data = amounts(value['by_currency'])
    assert data['CZK']['average_check'] == Decimal('2000.00') and data['CZK']['business_count'] == 1
    assert data['EUR']['average_check'] == Decimal('40.00')
    # count-only metrics are fine across currencies
    assert value['average_visits_per_client'] == 3.0


def test_benchmarks_hide_a_currency_with_too_few_salons(db, world):
    # only one EUR salon: showing its average check would point right at it
    euro = other_salon(db, world, 'Euro Salon', 'EUR')
    other_salon(db, world, 'Quiet Salon', 'CZK')
    visit(db, world, 1000, 'CZK')
    visit(db, world, 40, 'EUR', business_id=euro)
    visit(db, world, 7, None, business_id=euro)
    value = get_anonymous_benchmarks(db, min_businesses=2, locale='en')
    assert value['available'] is True
    assert value['average_check'] is None
    assert value['by_currency'] == []


def test_benchmarks_with_one_currency_keep_the_old_average(db, world):
    second = other_salon(db, world, 'Second CZK Salon', 'CZK')
    visit(db, world, 1000, 'CZK')
    visit(db, world, 3000, 'CZK', business_id=second)
    value = get_anonymous_benchmarks(db, min_businesses=2, locale='en')
    assert value['currency'] == 'CZK' and value['average_check'] == 2000
    assert amounts(value['by_currency'])['CZK']['business_count'] == 2


# --- popular services ------------------------------------------------------

def test_popular_services_are_per_currency(db, world):
    mixed(db, world)
    visit(db, world, None, None, status='planned')
    rows = get_popular_services(db, business_id=world['business']['id'])
    PopularServicesResponse.model_validate({'services': rows})
    [service] = rows
    assert service['visit_count'] == 5
    assert service['total_revenue'] is None and service['average_price'] is None
    data = amounts(service['by_currency'])
    assert data['CZK']['amount'] == Decimal('1500.00') and data['CZK']['average'] == Decimal('750.00')
    assert data['EUR']['amount'] == Decimal('40.00')
    assert data[None]['requires_review'] and data[None]['count'] == 1


def test_popular_services_keep_old_fields_for_one_currency(db, world):
    visit(db, world, 1000, 'CZK')
    visit(db, world, 500, 'CZK')
    visit(db, world, None, None, status='planned')  # no price yet, doesn't taint anything
    [service] = get_popular_services(db, business_id=world['business']['id'])
    assert service['currency'] == 'CZK' and service['visit_count'] == 3
    assert service['total_revenue'] == 1500 and service['average_price'] == 750


# --- client spending -------------------------------------------------------

def test_client_spending_is_per_currency(db, world):
    mixed(db, world)
    value = get_client_statistics(db, business_id=world['business']['id'])
    ClientStatisticsResponse.model_validate(value)
    assert value['average_spending'] is None and value['top_spenders'] == []
    assert value['currency'] is None
    groups = {row['currency']: row['clients'] for row in value['top_spenders_by_currency']}
    assert groups['CZK'][0]['amount'] == Decimal('1500.00')
    assert groups['EUR'][0]['amount'] == Decimal('40.00')
    assert groups[None][0]['requires_review'] and groups[None][0]['amount'] is None
    average = amounts(value['average_spending_by_currency'])
    assert average['CZK']['average'] == Decimal('1500.00') and average['CZK']['client_count'] == 1
    assert average[None]['requires_review'] and average[None]['average'] is None


def test_client_spending_for_one_currency_keeps_old_fields(db, world):
    visit(db, world, 1000, 'CZK')
    visit(db, world, 300, 'CZK')
    value = get_client_statistics(db, business_id=world['business']['id'])
    assert value['currency'] == 'CZK' and value['average_spending'] == 1300
    assert value['top_spenders'] == [{'client_id': world['customer']['id'],
                                      'client_name': 'Privacy Client', 'spending': 1300.0}]


# --- payment stats ---------------------------------------------------------

def test_payment_statistics_are_per_currency(db, world):
    mixed(db, world)
    value = get_payment_statistics(db, business_id=world['business']['id'])
    PaymentStatisticsResponse.model_validate(value)
    assert value['total_payments'] is None and value['currency'] is None
    assert value['total_count'] == 4
    assert value['cash_payments']['total'] is None and value['cash_payments']['count'] == 3
    assert value['cash_payments']['percentage'] is None
    data = amounts(value['by_currency'])
    assert data['CZK']['total'] == Decimal('1500.00') and data['CZK']['count'] == 2
    assert data['CZK']['payments_by_method']['card']['total'] == Decimal('500.00')
    assert data['CZK']['payments_by_method']['card']['percentage'] == pytest.approx(33.33)
    assert data['EUR']['payments_by_method']['cash']['percentage'] == 100.0
    assert data[None]['requires_review'] and data[None]['total'] is None


def test_payment_statistics_for_one_currency_keep_old_fields(db, world):
    visit(db, world, 1000, 'CZK')
    visit(db, world, 500, 'CZK', method='card')
    value = get_payment_statistics(db, business_id=world['business']['id'])
    assert value['currency'] == 'CZK' and value['total_payments'] == 1500
    assert value['card_payments'] == {'total': 500.0, 'count': 1, 'percentage': 33.33}


# --- HTTP and exports ------------------------------------------------------

def test_endpoints_never_return_a_mixed_sum(client, db, world):
    mixed(db, world)
    headers = world['oh']
    popular = client.get('/analytics/services/popular', headers=headers)
    assert popular.status_code == 200, popular.text
    assert popular.json()['services'][0]['total_revenue'] is None
    stats = client.get('/analytics/clients/statistics', headers=headers).json()
    assert stats['average_spending'] is None
    paid = client.get('/analytics/payments/statistics', headers=headers).json()
    assert paid['total_payments'] is None
    both = client.get('/analytics/services-specialists', headers=headers).json()
    assert both['services'][0]['total_revenue'] is None
    # 1540 / 1547 would mean CZK and EUR (and the unknown one) got added up somewhere
    for body in (popular.text, str(stats), str(paid), str(both)):
        assert '1540' not in body and '1547' not in body


def read_export(response, format):
    if format == 'csv':
        return list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))
    rows = list(load_workbook(io.BytesIO(response.content)).active.iter_rows(values_only=True))
    return [dict(zip(rows[0], row)) for row in rows[1:]]


@pytest.mark.parametrize('format', ['csv', 'excel'])
def test_services_and_clients_exports_have_one_row_per_currency(client, db, world, format):
    mixed(db, world)
    for kind, column in (('services', 'Total Revenue'), ('clients', 'Total Spending')):
        response = client.post('/analytics/reports/export', headers=world['oh'],
                               json={'report_type': kind, 'format': format})
        assert response.status_code == 200, response.text
        rows = read_export(response, format)
        data = {row['Currency']: row for row in rows}
        assert set(data) == {'CZK', 'EUR', 'UNKNOWN'}, rows
        assert data['CZK'][column] == '1500.00' and data['EUR'][column] == '40.00'
        assert data['UNKNOWN'][column] in (None, '')
        assert str(data['UNKNOWN']['Requires Review']).lower() == 'true'
