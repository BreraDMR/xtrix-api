"""Trends and forecasts never add prices of different (or unknown) currencies together."""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.api.core import advanced_analytics
from app.api.core.advanced_analytics import get_forecast, get_trends_analysis
from app.schemas.analytics import ForecastResponse, TrendsAnalysisResponse
from tests import test_financial_currency_report as currency_report

# shared fixtures: a salon with an owner, a staff member and one client
salon = currency_report.salon
world = currency_report.world
quote = currency_report.quote
units = currency_report.units

NOW = datetime(2026, 11, 20, 12, 0)
CURRENT = datetime(2026, 11, 5, 9, 0)   # this month
PREVIOUS = datetime(2026, 10, 5, 9, 0)  # last month


@pytest.fixture(autouse=True)
def frozen_now(monkeypatch):
    monkeypatch.setattr(advanced_analytics, 'utcnow', lambda: NOW)


def at(day, hours):
    # one hour apart, the same specialist can't hold two visits at once
    return day + timedelta(hours=hours)


def trends(db, w, metric):
    value = get_trends_analysis(db, business_id=w['business']['id'], metric=metric, comparison_type='MoM')
    TrendsAnalysisResponse.model_validate(value)
    return value


def forecast(db, w, periods=2):
    value = get_forecast(db, business_id=w['business']['id'], forecast_type='revenue', periods=periods)
    ForecastResponse.model_validate(value)
    return value


def test_revenue_trend_keeps_two_currencies_apart(db, world):
    quote(db, world, 1000, 'CZK', start=at(CURRENT, 0))
    quote(db, world, 500, 'CZK', start=at(PREVIOUS, 0))
    quote(db, world, 40, 'EUR', start=at(CURRENT, 1))
    quote(db, world, 60, 'EUR', start=at(PREVIOUS, 1))
    value = trends(db, world, 'revenue')
    # no invented grand total
    assert value['current_value'] is None and value['previous_value'] is None
    assert value['change'] is None and value['change_percent'] is None and value['trend_direction'] is None
    assert value['currency'] is None
    data = units(value['by_currency'])
    assert set(data) == {'CZK', 'EUR'}
    assert data['CZK']['current_value'] == Decimal('1000.00') and data['CZK']['previous_value'] == Decimal('500.00')
    assert data['CZK']['change'] == Decimal('500.00') and data['CZK']['change_percent'] == 100.0
    assert data['CZK']['trend_direction'] == 'up' and not data['CZK']['requires_review']
    assert data['EUR']['current_value'] == Decimal('40.00') and data['EUR']['previous_value'] == Decimal('60.00')
    assert data['EUR']['trend_direction'] == 'down'
    assert data['EUR']['current_count'] == 1 and data['EUR']['previous_count'] == 1


def test_average_check_trend_is_per_currency(db, world):
    quote(db, world, 1000, 'CZK', start=at(CURRENT, 0))
    quote(db, world, 2000, 'CZK', start=at(CURRENT, 1))
    quote(db, world, 30, 'EUR', start=at(CURRENT, 2))
    value = trends(db, world, 'average_check')
    assert value['current_value'] is None
    data = units(value['by_currency'])
    assert data['CZK']['current_value'] == Decimal('1500.00') and data['CZK']['current_count'] == 2
    assert data['CZK']['previous_value'] == Decimal('0.00') and data['CZK']['previous_count'] == 0
    assert data['EUR']['current_value'] == Decimal('30.00')


def test_visits_without_currency_are_a_review_row_not_a_number(db, world):
    quote(db, world, 1000, 'CZK', start=at(CURRENT, 0))
    quote(db, world, 70, None, start=at(CURRENT, 1))
    for metric in ('revenue', 'average_check'):
        value = trends(db, world, metric)
        assert value['current_value'] is None and value['currency'] is None
        data = units(value['by_currency'])
        assert data['CZK']['current_value'] == Decimal('1000.00')
        assert data[None]['requires_review'] and data[None]['current_value'] is None
        assert data[None]['change'] is None and data[None]['trend_direction'] is None
        assert data[None]['current_count'] == 1


def test_one_known_currency_keeps_the_old_scalar_fields(db, world):
    quote(db, world, 1000.01, 'CZK', start=at(CURRENT, 0))
    quote(db, world, 500, 'CZK', start=at(PREVIOUS, 0))
    value = trends(db, world, 'revenue')
    assert value['currency'] == 'CZK'
    assert value['current_value'] == pytest.approx(1000.01) and value['previous_value'] == 500
    assert value['change'] == pytest.approx(500.01) and value['trend_direction'] == 'up'
    assert len(value['by_currency']) == 1


def test_one_currency_with_a_broken_price_has_no_scalar(db, world):
    quote(db, world, 1000, 'CZK', start=at(CURRENT, 0))
    quote(db, world, float('nan'), 'CZK', start=at(CURRENT, 1))
    value = trends(db, world, 'revenue')
    assert value['current_value'] is None and value['currency'] is None
    assert value['by_currency'][0]['requires_review'] and value['by_currency'][0]['current_value'] is None


def test_deleted_visits_do_not_count(db, world):
    quote(db, world, 1000, 'CZK', start=at(CURRENT, 0))
    quote(db, world, 9000, 'EUR', start=at(CURRENT, 1), deleted=True)
    value = trends(db, world, 'revenue')
    assert value['currency'] == 'CZK' and value['current_value'] == 1000


def test_empty_trend_keeps_zero_without_claiming_a_currency(db, world):
    value = trends(db, world, 'revenue')
    assert value['current_value'] == 0 and value['previous_value'] == 0
    assert value['currency'] is None and value['by_currency'] == []
    assert value['trend_direction'] == 'stable'


def test_count_metrics_are_untouched_by_currency(db, world):
    quote(db, world, 1000, 'CZK', start=at(CURRENT, 0))
    quote(db, world, 40, 'EUR', start=at(CURRENT, 1))
    value = trends(db, world, 'visits')
    assert value['current_value'] == 2 and value['by_currency'] == [] and value['currency'] is None


def test_revenue_forecast_keeps_two_currencies_apart(db, world):
    for hours, (day, price) in enumerate([(PREVIOUS, 300), (CURRENT, 600)]):
        quote(db, world, price, 'CZK', start=at(day, hours))
    quote(db, world, 20, 'EUR', start=at(CURRENT, 5))
    value = forecast(db, world)
    assert value['currency'] is None
    assert value['historical_average'] is None and value['last_value'] is None
    assert [row['forecasted_value'] for row in value['forecast']] == [None, None]
    assert [row['period'] for row in value['forecast']] == ['2026-12', '2027-01']
    data = units(value['by_currency'])
    assert data['CZK']['historical_average'] == Decimal('450.00') and data['CZK']['last_value'] == Decimal('600.00')
    assert [row['forecasted_value'] for row in data['CZK']['forecast']] == [Decimal('450.00')] * 2
    assert data['CZK']['months'] == 2 and not data['CZK']['requires_review']
    assert data['EUR']['historical_average'] == Decimal('20.00') and data['EUR']['months'] == 1


def test_forecast_with_unknown_currency_has_a_review_row(db, world):
    quote(db, world, 600, 'CZK', start=at(CURRENT, 0))
    quote(db, world, 70, None, start=at(CURRENT, 1))
    value = forecast(db, world, periods=1)
    assert value['historical_average'] is None and value['forecast'][0]['forecasted_value'] is None
    data = units(value['by_currency'])
    assert data['CZK']['last_value'] == Decimal('600.00')
    assert data[None]['requires_review'] and data[None]['historical_average'] is None
    assert data[None]['forecast'][0]['forecasted_value'] is None


def test_forecast_with_one_currency_keeps_the_old_shape(db, world):
    quote(db, world, 300, 'CZK', start=at(PREVIOUS, 0))
    quote(db, world, 600.5, 'CZK', start=at(CURRENT, 1))
    value = forecast(db, world, periods=1)
    assert value['currency'] == 'CZK'
    assert value['historical_average'] == pytest.approx(450.25) and value['last_value'] == pytest.approx(600.5)
    assert value['forecast'] == [{'period': '2026-12', 'forecasted_value': pytest.approx(450.25)}]


def test_empty_forecast_and_visit_forecast_stay_as_before(db, world):
    value = forecast(db, world)
    assert value['forecast'] == [] and value['historical_average'] == 0 and value['by_currency'] == []
    quote(db, world, 600, 'CZK', start=at(CURRENT, 0))
    quote(db, world, 40, 'EUR', start=at(CURRENT, 1))
    visits = get_forecast(db, business_id=world['business']['id'], forecast_type='visits', periods=1)
    ForecastResponse.model_validate(visits)
    assert visits['last_value'] == 2 and visits['by_currency'] == [] and visits['currency'] is None


def test_endpoints_serialize_currency_slices(client, db, world):
    quote(db, world, 1000, 'CZK', start=at(CURRENT, 0))
    quote(db, world, 40, 'EUR', start=at(CURRENT, 1))
    body = client.get('/analytics/trends?metric=revenue', headers=world['oh'])
    assert body.status_code == 200, body.text
    data = {row['currency']: row for row in body.json()['by_currency']}
    assert body.json()['current_value'] is None and data['CZK']['current_value'] == '1000.00'
    body = client.get('/analytics/forecast?forecast_type=revenue', headers=world['oh'])
    assert body.status_code == 200, body.text
    assert body.json()['historical_average'] is None
    assert {row['currency'] for row in body.json()['by_currency']} == {'CZK', 'EUR'}
