"""Settled visits get a new quote only through an explicit, append-only correction."""
from decimal import Decimal

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from app.api.core.i18n import _
from app.api.core.visit_settlement_projection import SettlementScope, read_settlement
from app.db.models.business_audit_event import BusinessAuditEvent
from app.db.models.service_payment import ServicePaymentAttempt
from app.db.models.visit import Visit
from app.db.models.visit_price_correction import VisitPriceCorrection
from tests import test_completed_payment_guard as base
from tests import test_visit_reprice_guard_api as guards
from tests import test_visit_settlement_projection as facts

salon = base.salon


def price_url(visit_id):
    return f'/visits/{visit_id}/price'


def url(visit_id):
    return f'/visits/{visit_id}/price-corrections'


def quote(client, salon, visit_id):
    r = client.get(price_url(visit_id), headers=salon['headers'])
    assert r.status_code == 200, r.text
    return r.json()


def body(q, currency='EUR', price='20.00', key='corr-key-0001', action='refund_due', reason='Client pays in euro'):
    return {'currency': currency, 'expected_quote': q['expected_quote'],
            'items': [{'item_id': item['item_id'], 'price': price} for item in q['items']],
            'idempotency_key': key, 'prior_payment_action': action, 'reason': reason}


def paid_visit(client, db, salon, kind='paid'):
    visit_id, _items = base.create_quote(client, salon)
    guards.evidence(db, salon, visit_id, kind)
    return visit_id


def attempts(db, visit_id):
    db.expire_all()
    rows = [dict(row) for row in db.execute(select(ServicePaymentAttempt.__table__).where(
        ServicePaymentAttempt.visit_id == visit_id).order_by(ServicePaymentAttempt.id)).mappings()]
    db.rollback()
    return rows


@pytest.mark.parametrize('kind', ['paid', 'manual', 'refunded', 'earned', 'spent'])
def test_settled_visit_is_corrected_and_old_money_stays_as_it_was(client, db, salon, kind):
    visit_id = paid_visit(client, db, salon, kind)
    old = quote(client, salon, visit_id)
    assert old['currency'] == 'CZK'
    before_attempts = attempts(db, visit_id)
    r = client.post(url(visit_id), headers=salon['headers'], json=body(old))
    assert r.status_code == 200, r.text
    assert r.headers['cache-control'] == 'no-store'
    data = r.json()
    assert data['replayed'] is False
    assert data['quote']['currency'] == 'EUR' and Decimal(data['quote']['total']) == 40
    assert data['correction']['old_currency'] == 'CZK'
    assert Decimal(data['correction']['old_total']) == Decimal(old['total'])
    assert data['correction']['new_currency'] == 'EUR' and Decimal(data['correction']['new_total']) == 40
    assert data['correction']['prior_payment_action'] == 'refund_due'
    # the stored quote really changed, the old receipts did not
    assert quote(client, salon, visit_id) == data['quote']
    assert attempts(db, visit_id) == before_attempts
    db.expire_all()
    visit = db.get(Visit, visit_id)
    if kind == 'manual':
        assert visit.manual_payment_recorded_at is not None
    row = db.execute(select(VisitPriceCorrection).where(VisitPriceCorrection.visit_id == visit_id)).scalar_one()
    assert row.actor_user_id == salon['owner'].id and row.reason == 'Client pays in euro'
    assert row.settlement['currency'] == 'CZK' and row.settlement['visit_id'] == visit_id
    assert [Decimal(item['price']) for item in row.old_items] == [Decimal(str(item['price'])) for item in old['items']]
    assert db.query(BusinessAuditEvent).filter_by(action='visit.price_corrected', entity_id=visit_id).count() == 1
    listed = client.get(url(visit_id), headers=salon['headers'])
    assert listed.status_code == 200 and [item['id'] for item in listed.json()] == [data['correction']['id']]


def test_same_key_and_body_replays_without_a_second_row(client, db, salon):
    visit_id = paid_visit(client, db, salon)
    payload = body(quote(client, salon, visit_id))
    first = client.post(url(visit_id), headers=salon['headers'], json=payload)
    second = client.post(url(visit_id), headers=salon['headers'], json=payload)
    assert first.status_code == 200 and second.status_code == 200, second.text
    assert second.json()['replayed'] is True
    assert second.json()['correction'] == first.json()['correction']
    assert second.json()['quote'] == first.json()['quote']
    assert db.query(VisitPriceCorrection).filter_by(visit_id=visit_id).count() == 1
    assert db.query(BusinessAuditEvent).filter_by(action='visit.price_corrected', entity_id=visit_id).count() == 1


@pytest.mark.parametrize('locale', ['en', 'ru', 'cz'])
def test_reused_key_with_other_details_conflicts(client, db, salon, locale):
    visit_id = paid_visit(client, db, salon)
    payload = body(quote(client, salon, visit_id))
    assert client.post(url(visit_id), headers=salon['headers'], json=payload).status_code == 200
    changed = {**payload, 'reason': 'Something else entirely'}
    r = client.post(url(visit_id), headers={**salon['headers'], 'Accept-Language': locale}, json=changed)
    assert r.status_code == 409, r.text
    assert r.json()['error']['message'] == _('visit_price_correction_key_conflict', locale)
    assert db.query(VisitPriceCorrection).filter_by(visit_id=visit_id).count() == 1


def test_stale_quote_and_unchanged_quote_are_refused_without_writes(client, db, salon):
    visit_id = paid_visit(client, db, salon)
    old = quote(client, salon, visit_id)
    assert client.post(url(visit_id), headers=salon['headers'], json=body(old)).status_code == 200
    before = base.snapshot(db, visit_id)
    stale = client.post(url(visit_id), headers=salon['headers'], json=body(old, key='corr-key-0002'))
    assert stale.status_code == 409 and stale.json()['error']['message'] == _('visit_price_quote_changed', 'en')
    current = quote(client, salon, visit_id)
    same = client.post(url(visit_id), headers=salon['headers'], json=body(current, key='corr-key-0003'))
    assert same.status_code == 409 and same.json()['error']['message'] == _('visit_price_correction_unchanged', 'en')
    assert base.snapshot(db, visit_id) == before
    assert db.query(VisitPriceCorrection).filter_by(visit_id=visit_id).count() == 1


def test_second_correction_back_to_old_currency_is_its_own_row(client, db, salon):
    visit_id = paid_visit(client, db, salon)
    assert client.post(url(visit_id), headers=salon['headers'], json=body(quote(client, salon, visit_id))).status_code == 200
    back = client.post(url(visit_id), headers=salon['headers'], json=body(
        quote(client, salon, visit_id), currency='CZK', price='500.00', key='corr-key-0002', action='retained'))
    assert back.status_code == 200, back.text
    rows = db.query(VisitPriceCorrection).filter_by(visit_id=visit_id).order_by(VisitPriceCorrection.id).all()
    assert [(row.old_currency, row.new_currency, row.prior_payment_action) for row in rows] == [
        ('CZK', 'EUR', 'refund_due'), ('EUR', 'CZK', 'retained')]


def test_unsettled_visit_points_to_the_plain_editor(client, db, salon):
    visit_id, _items = base.create_quote(client, salon)
    before = base.snapshot(db, visit_id)
    r = client.post(url(visit_id), headers={**salon['headers'], 'Accept-Language': 'ru'},
                    json=body(quote(client, salon, visit_id)))
    assert r.status_code == 409, r.text
    assert r.json()['error']['message'] == _('visit_price_correction_not_needed', 'ru')
    assert base.snapshot(db, visit_id) == before
    assert db.query(VisitPriceCorrection).count() == 0


def test_pending_checkout_must_finish_first(client, db, salon):
    visit_id = paid_visit(client, db, salon, 'pending')
    before = base.snapshot(db, visit_id)
    r = client.post(url(visit_id), headers=salon['headers'], json=body(quote(client, salon, visit_id)))
    assert r.status_code == 409 and r.json()['error']['message'] == _('visit_price_payment_pending', 'en')
    assert base.snapshot(db, visit_id) == before


def test_plain_editor_still_refuses_the_settled_visit(client, db, salon):
    visit_id = paid_visit(client, db, salon)
    q = quote(client, salon, visit_id)
    plain = {key: value for key, value in body(q).items() if key in ('currency', 'expected_quote', 'items')}
    r = client.put(price_url(visit_id), headers=salon['headers'], json=plain)
    assert r.status_code == 409 and r.json()['error']['message'] == _('visit_price_settlement_required', 'en')


@pytest.mark.parametrize('broken', [
    {'reason': '  '}, {'reason': 'x' * 501}, {'idempotency_key': 'short'}, {'idempotency_key': 'bad key with spaces'},
    {'prior_payment_action': 'forget'}, {'prior_payment_action': None}, {'unexpected': 1},
])
def test_shape_is_strict(client, db, salon, broken):
    visit_id = paid_visit(client, db, salon)
    payload = {**body(quote(client, salon, visit_id)), **broken}
    r = client.post(url(visit_id), headers=salon['headers'], json=payload)
    assert r.status_code == 422, r.text
    assert db.query(VisitPriceCorrection).count() == 0


def test_staff_without_financials_and_other_salons_cannot_correct_or_read(client, db, salon, make_user, headers_for):
    visit_id = paid_visit(client, db, salon)
    payload = body(quote(client, salon, visit_id))
    staff = client.post('/employees/create-with-user', headers=salon['headers'], json={
        'full_name': 'No money staff', 'email': 'nomoney.staff@example.com', 'base_salary': 0,
        'permissions': {'can_create_visits': True, 'can_view_financials': False}})
    assert staff.status_code == 200, staff.text
    from app.db.models.user import User
    staff_headers = headers_for(db.get(User, staff.json()['employee_user_id']))
    other = make_user('business_owner')
    other_headers = headers_for(other)
    assert client.post('/businesses/', headers=other_headers, json={'name': 'Other', 'business_type': 'salon'}).status_code == 200
    for headers in (staff_headers, other_headers):
        assert client.post(url(visit_id), headers=headers, json=payload).status_code in (403, 404)
        assert client.get(url(visit_id), headers=headers).status_code in (403, 404)
    assert client.post(url(visit_id), json=payload).status_code == 401
    assert db.query(VisitPriceCorrection).count() == 0


def test_rows_are_append_only_in_the_database(client, db, salon):
    visit_id = paid_visit(client, db, salon)
    assert client.post(url(visit_id), headers=salon['headers'], json=body(quote(client, salon, visit_id))).status_code == 200
    for statement in ("UPDATE visit_price_corrections SET reason='rewritten'", 'DELETE FROM visit_price_corrections'):
        with pytest.raises(DBAPIError):
            db.execute(text(statement))
        db.rollback()
    assert db.query(VisitPriceCorrection).one().reason == 'Client pays in euro'


# ---- projection: money under a replaced currency is shown apart, never mixed ----

world = facts.world


def corrected(db, w, old='EUR', new='CZK'):
    scope = w['scope']
    db.execute(update(Visit).where(Visit.id == scope.id).values(currency_snapshot=new))
    db.add(VisitPriceCorrection(business_id=scope.business_id, visit_id=scope.id, idempotency_key='proj-key-1',
        request_hash='a' * 64, old_currency=old, old_total=Decimal('1000.01'), old_items=[], new_currency=new,
        new_total=Decimal('1000.01'), new_items=[], settlement={}, prior_payment_action='refund_due',
        reason='projection test', created_at=facts.NOW))
    db.commit()


def test_prior_currency_payment_is_reported_apart_until_refunded(db, world):
    row = facts.attempt(db, world, value='20.01')
    corrected(db, world)
    result = read_settlement(db, world['scope'])
    assert result.currency == 'CZK'
    assert result.provider_received == 0 and result.provider_net == 0
    assert 'provider_history_review' not in result.reasons
    assert 'prior_currency_settlement' in result.reasons
    [prior] = result.prior_currency_settlements
    assert (prior.currency, prior.provider_received, prior.provider_net) == ('EUR', Decimal('20.01'), Decimal('20.01'))
    facts.refund(db, row, value='20.01')
    db.execute(update(ServicePaymentAttempt).where(ServicePaymentAttempt.id == row.id).values(status='refunded'))
    db.commit()
    result = read_settlement(db, world['scope'])
    [prior] = result.prior_currency_settlements
    assert prior.provider_net == 0 and prior.provider_refunded == Decimal('20.01')
    assert 'prior_currency_settlement' not in result.reasons


def test_prior_currency_bonus_and_gift_are_flagged_not_mixed(db, world):
    facts.bonus(db, world, value='3.00')
    facts.gift(db, world, value='4.00')
    corrected(db, world)
    result = read_settlement(db, world['scope'])
    assert result.gift_money_coverage == 0 and 'gift_history_review' not in result.reasons
    assert 'prior_currency_benefits' in result.reasons
    assert result.prior_currency_settlements[0].benefit_rows == 2


def test_without_corrections_a_foreign_currency_payment_still_needs_review(db, world):
    facts.attempt(db, world)
    db.execute(update(Visit).where(Visit.id == world['scope'].id).values(currency_snapshot='CZK'))
    db.commit()
    result = read_settlement(db, world['scope'])
    assert 'provider_history_review' in result.reasons and result.prior_currency_settlements == ()


def test_busy_visit_returns_retry_and_writes_nothing(client, db, salon):
    from sqlalchemy.orm import Session
    visit_id = paid_visit(client, db, salon)
    payload = body(quote(client, salon, visit_id))
    before = base.snapshot(db, visit_id)
    with Session(db.get_bind()) as holder:
        holder.execute(select(Visit.id).where(Visit.id == visit_id).with_for_update())
        r = client.post(url(visit_id), headers={**salon['headers'], 'Accept-Language': 'ru'}, json=payload)
        assert r.status_code == 409, r.text
        assert r.json()['error']['message'] == _('visit_price_retry', 'ru')
    assert base.snapshot(db, visit_id) == before
    assert db.query(VisitPriceCorrection).count() == 0
    # once the lock is gone the same request goes through
    assert client.post(url(visit_id), headers=salon['headers'], json=payload).status_code == 200


def test_migration_roundtrip_when_empty_and_guard_when_rows_exist(client, db, salon):
    from alembic import command
    from alembic.config import Config
    config = Config('alembic.ini')
    db.close()
    command.downgrade(config, '0089_released_slot_jobs')
    assert db.execute(text("SELECT to_regclass('visit_price_corrections')")).scalar_one() is None
    db.rollback()
    command.upgrade(config, 'head')
    visit_id = paid_visit(client, db, salon)
    assert client.post(url(visit_id), headers=salon['headers'], json=body(quote(client, salon, visit_id))).status_code == 200
    db.close()
    with pytest.raises(DBAPIError, match='Price corrections exist'):
        command.downgrade(config, '0089_released_slot_jobs')
    assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '0090_visit_price_corrections'
    assert db.query(VisitPriceCorrection).count() == 1
    db.rollback()
