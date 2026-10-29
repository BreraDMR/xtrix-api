"""Completed UPDATE validates the stored result, including snapshot-preserving items."""
from datetime import timedelta

import pytest

from app.api.crud import crud_visit
from app.db.models.audit_log import AuditLog
from app.db.models.business_audit_event import BusinessAuditEvent
from app.db.models.loyalty_program import LoyaltyTransaction
from app.db.models.visit import Visit
from app.main import app
from app.schemas.visit import VisitUpdate
from tests import test_multi_service as multi

salon = multi.salon


def create_quote(client, salon, *, single=False):
    assert not app.dependency_overrides
    headers = salon['headers']
    business_id = salon['business']['id']
    multi._ok(client.put(f'/businesses/{business_id}', headers=headers, json={'currency': 'CZK'}), 'CZK')
    multi._ok(client.post('/loyalty/program', headers=headers, json={
        'business_id': business_id, 'program_type': 'points', 'points_per_currency': 1,
    }), 'program')
    items = [{'service_id': salon['first']['id']}]
    if not single:
        items.append({'service_id': salon['second']['id']})
    visit = multi._ok(client.post('/visits/', headers=headers, json={
        'client_id': salon['client']['id'], 'specialist_id': salon['owner'].id,
        'date_start': salon['start'].isoformat(), 'items': items,
    }), 'create')
    assert visit['price'] > 0 and visit['payment_method'] is None
    return visit['id'], items


def snapshot(db, visit_id):
    db.expire_all()
    visit = db.get(Visit, visit_id)
    stored = tuple((column.name, getattr(visit, column.name)) for column in Visit.__table__.columns)
    items = tuple(tuple((column.name, getattr(item, column.name)) for column in item.__table__.columns)
                  for item in sorted(visit.items, key=lambda row: row.position))
    counts = (db.query(BusinessAuditEvent).count(), db.query(AuditLog).count(),
              db.query(LoyaltyTransaction).filter_by(visit_id=visit_id).count())
    db.rollback()
    return stored, items, counts


@pytest.mark.parametrize('kind', ['same_zero', 'same_null', 'same_omitted', 'changed_catalog', 'unknown_unit'])
def test_completed_without_method_rolls_back_entire_effective_update(client, db, salon, kind):
    from app.api.core.i18n import _
    visit_id, items = create_quote(client, salon)
    if kind == 'unknown_unit':
        db.get(Visit, visit_id).currency_snapshot = None
        db.commit()
    before = snapshot(db, visit_id)
    body = {'items': items, 'status': 'completed', 'note': 'must rollback',
            'date_start': (salon['start'] + timedelta(days=1)).isoformat()}
    if kind != 'same_omitted':
        body['price'] = None if kind == 'same_null' else 0
    if kind == 'changed_catalog':
        body['items'] = [{'service_id': salon['second']['id']}]
    headers = {**salon['headers'], 'Accept-Language': 'ru'}
    response = client.put(f'/visits/{visit_id}', headers=headers, json=body)
    assert response.status_code == 400, response.text
    assert _('payment_method_required_for_completed_visits', 'ru') in response.text
    assert snapshot(db, visit_id) == before


def test_valid_method_completes_with_stored_positive_quote_and_one_loyalty_record(client, db, salon):
    visit_id, items = create_quote(client, salon)
    before = snapshot(db, visit_id)
    response = client.put(f'/visits/{visit_id}', headers=salon['headers'], json={
        'items': items, 'price': 0, 'status': 'completed', 'payment_method': 'cash',
    })
    multi._ok(response, 'complete with method')
    db.expire_all()
    visit = db.get(Visit, visit_id)
    assert visit.status == 'completed' and visit.payment_method == 'cash'
    assert visit.price == dict(before[0])['price'] and visit.currency_snapshot == 'CZK'
    records = db.query(LoyaltyTransaction).filter_by(visit_id=visit_id).all()
    assert len(records) == 1 and records[0].points_amount == visit.price


def test_actual_single_free_price_still_completes_without_method_or_loyalty(client, db, salon):
    visit_id, _items = create_quote(client, salon, single=True)
    response = client.put(f'/visits/{visit_id}', headers=salon['headers'], json={
        'price': 0, 'status': 'completed',
    })
    multi._ok(response, 'actual free price')
    db.expire_all()
    visit = db.get(Visit, visit_id)
    assert visit.status == 'completed' and visit.price == 0 and visit.payment_method is None
    assert visit.currency_snapshot == 'CZK' and visit.items[0].price == 0
    assert db.query(LoyaltyTransaction).filter_by(visit_id=visit_id).count() == 0


def test_core_typed_guard_uses_existing_whole_rollback_before_audit(client, db, salon):
    visit_id, items = create_quote(client, salon)
    before = snapshot(db, visit_id)
    visit = db.get(Visit, visit_id)
    with pytest.raises(crud_visit.CompletedVisitPaymentRequired):
        crud_visit.update_visit(db, visit, VisitUpdate(items=items, status='completed', price=0,
            note='must rollback', date_start=salon['start'] + timedelta(days=1)))
    assert snapshot(db, visit_id) == before
