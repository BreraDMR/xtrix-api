"""An owner can state, on purpose, which currency their unlabelled old visits were in."""
from decimal import Decimal

import pytest
from sqlalchemy import select, update

from app.api.core.i18n import _
from app.db.models.business_audit_event import BusinessAuditEvent
from app.db.models.visit import Visit
from tests import test_completed_payment_guard as base

salon = base.salon
URL = '/businesses/me/legacy-visit-currency'


def more_visit(client, salon, days):
    from datetime import timedelta
    r = client.post('/visits/', headers=salon['headers'], json={
        'client_id': salon['client']['id'], 'specialist_id': salon['owner'].id,
        'date_start': (salon['start'] + timedelta(days=days)).isoformat(),
        'items': [{'service_id': salon['first']['id']}]})
    assert r.status_code == 200, r.text
    return r.json()['id']


def legacy_visits(client, db, salon, count=2):
    first, _items = base.create_quote(client, salon, single=True)
    ids = [first] + [more_visit(client, salon, day) for day in range(1, count)]
    db.execute(update(Visit).where(Visit.id.in_(ids)).values(currency_snapshot=None))
    db.commit()
    return ids


def units(db, ids):
    db.expire_all()
    result = dict(db.execute(select(Visit.id, Visit.currency_snapshot).where(Visit.id.in_(ids))).all())
    db.rollback()
    return [result[i] for i in ids]


def test_preview_counts_only_this_business_and_confirm_labels_exactly_those(client, db, salon, make_user, headers_for):
    ids = legacy_visits(client, db, salon, 3)
    known = more_visit(client, salon, 10)
    db.execute(update(Visit).where(Visit.id == ids[2]).values(deleted_at=Visit.date_start))
    db.commit()
    preview = client.get(URL, headers=salon['headers'])
    assert preview.status_code == 200, preview.text
    assert preview.headers['cache-control'] == 'no-store'
    assert preview.json()['unknown_visits'] == 3 and preview.json()['business_currency'] == 'CZK'
    r = client.post(URL + '/confirm', headers=salon['headers'], json={'currency': 'CZK', 'expected_count': 3})
    assert r.status_code == 200, r.text
    assert r.json() == {'currency': 'CZK', 'updated_visits': 3}
    assert units(db, ids + [known]) == ['CZK', 'CZK', 'CZK', 'CZK']
    event = db.execute(select(BusinessAuditEvent).where(
        BusinessAuditEvent.action == 'business.legacy_visit_currency_confirmed')).scalar_one()
    assert event.business_id == salon['business']['id'] and event.changes['updated_visits'] == 3
    assert client.get(URL, headers=salon['headers']).json()['unknown_visits'] == 0


def test_count_mismatch_changes_nothing(client, db, salon):
    ids = legacy_visits(client, db, salon, 2)
    r = client.post(URL + '/confirm', headers={**salon['headers'], 'Accept-Language': 'ru'},
                    json={'currency': 'CZK', 'expected_count': 1})
    assert r.status_code == 409, r.text
    assert r.json()['error']['message'] == _('legacy_currency_count_changed', 'ru')
    assert units(db, ids) == [None, None]
    assert db.query(BusinessAuditEvent).filter_by(action='business.legacy_visit_currency_confirmed').count() == 0


def test_nothing_to_confirm_is_a_conflict_not_a_silent_success(client, db, salon):
    base.create_quote(client, salon, single=True)
    r = client.post(URL + '/confirm', headers=salon['headers'], json={'currency': 'CZK', 'expected_count': 0})
    assert r.status_code == 409, r.text
    assert r.json()['error']['message'] == _('legacy_currency_nothing_to_confirm', 'en')


@pytest.mark.parametrize('body', [{'currency': 'XXX', 'expected_count': 1}, {'currency': 'CZ', 'expected_count': 1},
                                  {'currency': 'CZK', 'expected_count': -1}, {'currency': 'CZK'},
                                  {'currency': 'CZK', 'expected_count': 1, 'extra': True}])
def test_shape_is_strict(client, db, salon, body):
    ids = legacy_visits(client, db, salon, 1)
    assert client.post(URL + '/confirm', headers=salon['headers'], json=body).status_code == 422
    assert units(db, ids) == [None]


def test_other_salons_and_staff_cannot_preview_or_confirm(client, db, salon, make_user, headers_for):
    ids = legacy_visits(client, db, salon, 1)
    other = headers_for(make_user('business_owner'))
    assert client.post('/businesses/', headers=other, json={'name': 'Other', 'business_type': 'salon'}).status_code == 200
    other_preview = client.get(URL, headers=other)
    assert other_preview.status_code == 200 and other_preview.json()['unknown_visits'] == 0
    assert client.post(URL + '/confirm', headers=other, json={'currency': 'CZK', 'expected_count': 1}).status_code == 409
    staff = client.post('/employees/create-with-user', headers=salon['headers'], json={
        'full_name': 'Staff', 'email': 'legacy.staff@example.com',
        'permissions': {'can_create_visits': True, 'can_view_financials': True}})
    assert staff.status_code == 200, staff.text
    from app.db.models.user import User
    staff_headers = headers_for(db.get(User, staff.json()['employee_user_id']))
    assert client.get(URL, headers=staff_headers).status_code == 403
    assert client.post(URL + '/confirm', headers=staff_headers,
                       json={'currency': 'CZK', 'expected_count': 1}).status_code == 403
    assert client.get(URL).status_code == 401
    assert units(db, ids) == [None]


def test_confirmed_visits_count_in_the_financial_report(client, db, salon):
    ids = legacy_visits(client, db, salon, 2)
    for visit_id in ids:
        db.execute(update(Visit).where(Visit.id == visit_id).values(status='completed', payment_method='cash'))
    db.commit()
    before = client.get('/analytics/financial/report', headers=salon['headers']).json()
    assert before['total_revenue'] is None
    assert client.post(URL + '/confirm', headers=salon['headers'],
                       json={'currency': 'CZK', 'expected_count': 2}).status_code == 200
    after = client.get('/analytics/financial/report', headers=salon['headers']).json()
    assert after['currency'] == 'CZK' and Decimal(str(after['total_revenue'])) > 0
