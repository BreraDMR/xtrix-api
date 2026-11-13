"""Explicit charges and manual receipts/refunds with an exact amount, append-only per visit."""
from decimal import Decimal

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from app.api.core.i18n import _
from app.db.models.business_audit_event import BusinessAuditEvent
from app.db.models.visit import Visit
from app.db.models.visit_settlement_entry import VisitSettlementEntry
from tests import test_completed_payment_guard as base
from tests import test_visit_reprice_guard_api as guards

salon = base.salon


def url(visit_id):
    return f'/visits/{visit_id}/settlement-entries'


def body(kind='manual_receipt', amount='123.45', currency='CZK', key='entry-key-0001',
         reason='Paid in cash at the desk', **extra):
    return {'kind': kind, 'amount': amount, 'currency': currency, 'idempotency_key': key,
            'reason': reason, **extra}


def entries(db, visit_id):
    db.expire_all()
    rows = db.execute(select(VisitSettlementEntry).where(VisitSettlementEntry.visit_id == visit_id)
                      .order_by(VisitSettlementEntry.id)).scalars().all()
    return rows


def audit_count(db, visit_id):
    return db.query(BusinessAuditEvent).filter_by(action='visit.settlement_entry_recorded', entity_id=visit_id).count()


@pytest.mark.parametrize('kind', ['charge', 'manual_receipt', 'manual_refund'])
def test_entry_keeps_the_exact_amount_currency_and_author(client, db, salon, kind):
    visit_id, _items = base.create_quote(client, salon)
    r = client.post(url(visit_id), headers=salon['headers'], json=body(kind, '1000.05', reference='R-17'))
    assert r.status_code == 200, r.text
    assert r.headers['cache-control'] == 'no-store'
    data = r.json()
    assert data['replayed'] is False
    entry = data['entry']
    assert entry['kind'] == kind and entry['currency'] == 'CZK' and entry['visit_id'] == visit_id
    assert Decimal(entry['amount']) == Decimal('1000.05') and entry['reference'] == 'R-17'
    [row] = entries(db, visit_id)
    assert row.amount == Decimal('1000.05') and row.currency == 'CZK' and row.kind == kind
    assert row.actor_user_id == salon['owner'].id and row.business_id == salon['business']['id']
    assert len(row.request_hash) == 64
    assert audit_count(db, visit_id) == 1
    listed = client.get(url(visit_id), headers=salon['headers'])
    assert listed.status_code == 200 and listed.headers['cache-control'] == 'no-store'
    assert listed.json() == [entry]


def test_partial_entries_are_separate_rows_oldest_first(client, db, salon):
    visit_id, _items = base.create_quote(client, salon)
    for key, kind, amount in (('entry-key-a001', 'manual_receipt', '10.10'), ('entry-key-a002', 'manual_receipt', '0.01'),
                              ('entry-key-a003', 'manual_refund', '5.00')):
        assert client.post(url(visit_id), headers=salon['headers'], json=body(kind, amount, key=key)).status_code == 200
    listed = client.get(url(visit_id), headers=salon['headers']).json()
    assert [(item['kind'], Decimal(item['amount'])) for item in listed] == [
        ('manual_receipt', Decimal('10.10')), ('manual_receipt', Decimal('0.01')), ('manual_refund', Decimal('5.00'))]


def test_old_amountless_manual_marker_is_never_turned_into_an_amount(client, db, salon):
    visit_id, _items = base.create_quote(client, salon)
    guards.evidence(db, salon, visit_id, 'manual')
    db.expire_all()
    marked_at = db.get(Visit, visit_id).manual_payment_recorded_at
    assert marked_at is not None
    assert client.get(url(visit_id), headers=salon['headers']).json() == []
    assert client.post(url(visit_id), headers=salon['headers'], json=body(amount='50.00')).status_code == 200
    db.expire_all()
    assert db.get(Visit, visit_id).manual_payment_recorded_at == marked_at
    assert [row.amount for row in entries(db, visit_id)] == [Decimal('50.00')]


def test_same_key_and_body_replays_without_a_second_row(client, db, salon):
    visit_id, _items = base.create_quote(client, salon)
    first = client.post(url(visit_id), headers=salon['headers'], json=body())
    second = client.post(url(visit_id), headers=salon['headers'], json=body(amount=123.45))
    assert first.status_code == 200 and second.status_code == 200, second.text
    assert second.json()['replayed'] is True
    assert second.json()['entry'] == first.json()['entry']
    assert len(entries(db, visit_id)) == 1 and audit_count(db, visit_id) == 1


def test_replay_still_works_after_the_quote_currency_moved_on(client, db, salon):
    visit_id, _items = base.create_quote(client, salon)
    first = client.post(url(visit_id), headers=salon['headers'], json=body())
    db.execute(update(Visit).where(Visit.id == visit_id).values(currency_snapshot='EUR'))
    db.commit()
    again = client.post(url(visit_id), headers=salon['headers'], json=body())
    assert again.status_code == 200 and again.json()['entry'] == first.json()['entry']


@pytest.mark.parametrize('locale', ['en', 'ru', 'cz'])
@pytest.mark.parametrize('change', [{'amount': '123.46'}, {'kind': 'charge'}, {'reason': 'Something else'},
                                    {'reference': 'R-2'}])
def test_reused_key_with_other_details_conflicts(client, db, salon, locale, change):
    visit_id, _items = base.create_quote(client, salon)
    assert client.post(url(visit_id), headers=salon['headers'], json=body()).status_code == 200
    r = client.post(url(visit_id), headers={**salon['headers'], 'Accept-Language': locale}, json={**body(), **change})
    assert r.status_code == 409, r.text
    assert r.json()['error']['message'] == _('visit_settlement_entry_key_conflict', locale)
    assert len(entries(db, visit_id)) == 1


def test_same_key_on_another_visit_is_independent(client, db, salon):
    first, items = base.create_quote(client, salon)
    from datetime import timedelta
    created = client.post('/visits/', headers=salon['headers'], json={
        'client_id': salon['client']['id'], 'specialist_id': salon['owner'].id,
        'date_start': (salon['start'] + timedelta(days=1)).isoformat(), 'items': items})
    assert created.status_code in (200, 201), created.text
    second = created.json()['id']
    assert client.post(url(first), headers=salon['headers'], json=body()).status_code == 200
    assert client.post(url(second), headers=salon['headers'], json=body()).json()['replayed'] is False


@pytest.mark.parametrize('locale', ['en', 'ru', 'cz'])
def test_only_the_current_quote_currency_is_accepted(client, db, salon, locale):
    visit_id, _items = base.create_quote(client, salon)
    before = base.snapshot(db, visit_id)
    r = client.post(url(visit_id), headers={**salon['headers'], 'Accept-Language': locale}, json=body(currency='eur'))
    assert r.status_code == 409, r.text
    assert r.json()['error']['message'] == _('visit_settlement_currency_mismatch', locale)
    db.execute(update(Visit).where(Visit.id == visit_id).values(currency_snapshot=None))
    db.commit()
    unknown = client.post(url(visit_id), headers={**salon['headers'], 'Accept-Language': locale}, json=body())
    assert unknown.status_code == 409, unknown.text
    assert unknown.json()['error']['message'] == _('visit_settlement_currency_unknown', locale)
    assert entries(db, visit_id) == []
    db.execute(update(Visit).where(Visit.id == visit_id).values(currency_snapshot='CZK'))
    db.commit()
    assert base.snapshot(db, visit_id) == before


def test_lowercase_currency_of_the_quote_is_fine(client, db, salon):
    visit_id, _items = base.create_quote(client, salon)
    r = client.post(url(visit_id), headers=salon['headers'], json=body(currency='czk'))
    assert r.status_code == 200 and r.json()['entry']['currency'] == 'CZK'


@pytest.mark.parametrize('broken', [
    {'amount': '0'}, {'amount': '0.00'}, {'amount': '-1.00'}, {'amount': '1.001'}, {'amount': '100000000.00'},
    {'amount': 'abc'}, {'amount': True}, {'amount': None}, {'amount': 'NaN'},
    {'kind': 'refund'}, {'kind': None}, {'currency': 'XXQ'}, {'reason': '  '}, {'reason': 'x' * 501},
    {'idempotency_key': 'short'}, {'idempotency_key': 'bad key with spaces'}, {'reference': 'x' * 121},
    {'unexpected': 1},
])
def test_shape_is_strict(client, db, salon, broken):
    visit_id, _items = base.create_quote(client, salon)
    r = client.post(url(visit_id), headers=salon['headers'], json={**body(), **broken})
    assert r.status_code == 422, r.text
    assert db.query(VisitSettlementEntry).count() == 0


def test_staff_without_financials_and_other_salons_cannot_record_or_read(client, db, salon, make_user, headers_for):
    visit_id, _items = base.create_quote(client, salon)
    staff = client.post('/employees/create-with-user', headers=salon['headers'], json={
        'full_name': 'No money staff', 'email': 'nomoney.entries@example.com', 'base_salary': 0,
        'permissions': {'can_create_visits': True, 'can_view_financials': False}})
    assert staff.status_code == 200, staff.text
    from app.db.models.user import User
    staff_headers = headers_for(db.get(User, staff.json()['employee_user_id']))
    other = make_user('business_owner')
    other_headers = headers_for(other)
    assert client.post('/businesses/', headers=other_headers, json={'name': 'Other', 'business_type': 'salon'}).status_code == 200
    assert client.post(url(visit_id), headers=staff_headers, json=body()).status_code == 403
    assert client.get(url(visit_id), headers=staff_headers).status_code == 403
    assert client.post(url(visit_id), headers=other_headers, json=body()).status_code == 404
    assert client.get(url(visit_id), headers=other_headers).status_code == 404
    assert client.post(url(visit_id), json=body()).status_code == 401
    assert db.query(VisitSettlementEntry).count() == 0
    assert audit_count(db, visit_id) == 0


def test_deleted_visit_takes_no_entries(client, db, salon):
    visit_id, _items = base.create_quote(client, salon)
    assert client.delete(f'/visits/{visit_id}', headers=salon['headers']).status_code in (200, 204)
    assert client.post(url(visit_id), headers=salon['headers'], json=body()).status_code == 404
    assert db.query(VisitSettlementEntry).count() == 0


def test_an_entry_blocks_the_plain_price_editor(client, db, salon):
    visit_id, _items = base.create_quote(client, salon)
    q = client.get(f'/visits/{visit_id}/price', headers=salon['headers']).json()
    plain = {'currency': 'CZK', 'expected_quote': q['expected_quote'],
             'items': [{'item_id': item['item_id'], 'price': '1.00'} for item in q['items']]}
    assert client.post(url(visit_id), headers=salon['headers'], json=body(kind='charge')).status_code == 200
    r = client.put(f'/visits/{visit_id}/price', headers=salon['headers'], json=plain)
    assert r.status_code == 409, r.text
    assert r.json()['error']['message'] == _('visit_price_settlement_required', 'en')


def test_busy_visit_returns_retry_and_writes_nothing(client, db, salon):
    from sqlalchemy.orm import Session
    visit_id, _items = base.create_quote(client, salon)
    with Session(db.get_bind()) as holder:
        holder.execute(select(Visit.id).where(Visit.id == visit_id).with_for_update())
        r = client.post(url(visit_id), headers={**salon['headers'], 'Accept-Language': 'ru'}, json=body())
        assert r.status_code == 409, r.text
        assert r.json()['error']['message'] == _('visit_price_retry', 'ru')
    assert db.query(VisitSettlementEntry).count() == 0
    assert client.post(url(visit_id), headers=salon['headers'], json=body()).status_code == 200


def test_rows_are_append_only_in_the_database(client, db, salon):
    visit_id, _items = base.create_quote(client, salon)
    assert client.post(url(visit_id), headers=salon['headers'], json=body()).status_code == 200
    for statement in ("UPDATE visit_settlement_entries SET amount=1", 'DELETE FROM visit_settlement_entries'):
        with pytest.raises(DBAPIError):
            db.execute(text(statement))
        db.rollback()
    assert db.query(VisitSettlementEntry).one().amount == Decimal('123.45')


def test_database_refuses_nonsense_rows(client, db, salon):
    visit_id, _items = base.create_quote(client, salon)
    good = dict(business_id=salon['business']['id'], visit_id=visit_id, kind='charge', amount=Decimal('1.00'),
                currency='CZK', idempotency_key='db-key-0001', request_hash='a' * 64, reason='db check')
    for bad in ({'amount': Decimal('0')}, {'kind': 'gift'}, {'currency': 'czk'}, {'reason': '  '},
                {'request_hash': 'short'}):
        with pytest.raises(DBAPIError):
            db.execute(VisitSettlementEntry.__table__.insert().values(
                **{**good, **bad}, created_at=text('now()')))
        db.rollback()


def test_migration_roundtrip_when_empty_and_guard_when_rows_exist(client, db, salon):
    from alembic import command
    from alembic.config import Config
    config = Config('alembic.ini')
    db.close()
    command.downgrade(config, '0091_visitor_proof_mail_jobs')
    assert db.execute(text("SELECT to_regclass('visit_settlement_entries')")).scalar_one() is None
    db.rollback()
    command.upgrade(config, 'head')
    visit_id, _items = base.create_quote(client, salon)
    assert client.post(url(visit_id), headers=salon['headers'], json=body()).status_code == 200
    db.close()
    with pytest.raises(DBAPIError, match='Settlement entries exist'):
        command.downgrade(config, '0091_visitor_proof_mail_jobs')
    assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '0092_visit_settlement_entries'
    assert db.query(VisitSettlementEntry).count() == 1
    db.rollback()
