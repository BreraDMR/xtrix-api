"""Public history is one authorized SQL projection, never a CRM record serializer."""
from datetime import timedelta
from decimal import Decimal
import importlib

import pytest
from sqlalchemy import update

from app.db.models.business import Business
from app.db.models.visit import Visit
from tests import test_visitor_history as base


@pytest.fixture
def world(db):
    return base.world.__wrapped__(db)


def views():
    return importlib.import_module('app.api.core.visitor_history_views')


def test_public_detail_uses_only_historical_currency_after_business_change(db, world):
    base.grant(db, world, visit=world['a1'])
    db.execute(update(Visit).where(Visit.id == world['a1']).values(currency_snapshot='CZK'))
    db.execute(update(Business).where(Business.id == world['a']).values(currency='EUR'))
    db.commit()
    result = views().get_visit(db, world['one'], world['a1'])
    assert result.price.currency == 'CZK' and result.price.amount == Decimal('1234.56')
    assert result.start == base.NOW + timedelta(days=1)
    assert result.model_dump(mode='json')['start'].endswith('Z')


def test_public_projection_keeps_explicit_privacy_allowlist(db, world):
    base.grant(db, world, visit=world['a1'])
    db.commit()
    result = views().get_visit(db, world['one'], world['a1'])
    data = result.model_dump(mode='json')
    assert set(data) == {'id', 'business_id', 'business', 'start', 'end', 'service_name', 'status',
                         'confirmed_at', 'staff_name', 'price', 'items'}
    assert set(data['business']) == {'name', 'address', 'phone', 'email', 'timezone'}
    assert 'private-' not in result.model_dump_json()
    assert data['staff_name'] == 'Master A'


def _replace_items(db, world, visit_id, count=3):
    from sqlalchemy import delete
    from app.db.models.visit_item import VisitItem
    parent = db.get(Visit, visit_id)
    start = parent.date_start
    parent.status, parent.date_end = 'cancelled', start + timedelta(minutes=15 * count)
    db.flush()
    db.execute(delete(VisitItem).where(VisitItem.visit_id == visit_id))
    for position in reversed(range(count)):
        db.add(VisitItem(visit_id=visit_id, position=position, service_name=f'Sold item {position}',
            price=Decimal('10.25') + position, duration_minutes=15,
            specialist_id=world['staff_b'] if position == 1 else world['staff_a'],
            date_start=start + timedelta(minutes=15 * position),
            date_end=start + timedelta(minutes=15 * (position + 1))))
    db.flush()


def test_items_are_ordered_snapshots_including_cancelled_items_and_current_staff_names(db, world):
    from app.db.models.service import Service
    from app.db.models.user import User
    from app.db.models.visit_item import VisitItem
    base.grant(db, world, visit=world['a1'])
    parent = db.get(Visit, world['a1'])
    parent.currency_snapshot, parent.price, parent.service_name = 'CZK', 100, 'Sold parent label'
    _replace_items(db, world, parent.id)
    service = Service(name='Current changed service', specialist_id=world['staff_a'], business_id=world['a'],
                      price=9999, duration_minutes=123)
    db.add(service)
    db.flush()
    db.query(VisitItem).filter_by(visit_id=parent.id).update({'service_id': service.id})
    db.get(User, world['staff_b']).full_name = 'Current public Master B'
    db.commit()
    result = views().get_visit(db, world['one'], parent.id)
    assert result.status == 'cancelled' and result.service_name == 'Sold parent label'
    assert result.price.amount == Decimal('100')
    assert [item.position for item in result.items] == [0, 1, 2]
    assert [item.service_name for item in result.items] == ['Sold item 0', 'Sold item 1', 'Sold item 2']
    assert [item.price.amount for item in result.items] == [Decimal('10.25'), Decimal('11.25'), Decimal('12.25')]
    assert [item.staff_name for item in result.items] == ['Master A', 'Current public Master B', 'Master A']
    assert all(item.price.currency == 'CZK' and item.duration_minutes == 15 for item in result.items)
    assert not any(item.active for item in db.query(VisitItem).filter_by(visit_id=parent.id))
    assert '9999' not in result.model_dump_json() and 'Current changed service' not in result.model_dump_json()


@pytest.mark.parametrize('currency', [None, 'ZZZ'])
def test_unknown_historical_currency_never_inherits_business_currency_for_any_item(db, world, currency):
    base.grant(db, world, visit=world['a1'])
    db.get(Visit, world['a1']).currency_snapshot = currency
    db.get(Business, world['a']).currency = 'EUR'
    db.commit()
    result = views().get_visit(db, world['one'], world['a1'])
    assert result.price is None and all(item.price is None for item in result.items)


@pytest.mark.parametrize('amount', [None, float('nan'), float('inf'), float('-inf')])
def test_missing_or_nonfinite_total_is_not_zero_or_an_infinite_public_amount(db, world, amount):
    base.grant(db, world, visit=world['a1'])
    db.execute(update(Visit).where(Visit.id == world['a1']).values(currency_snapshot='CZK', price=amount))
    db.commit()
    assert views().get_visit(db, world['one'], world['a1']).price is None


def test_explicit_zero_and_finite_item_amounts_are_preserved_but_numeric_nan_is_hidden(db, world):
    from app.db.models.visit_item import VisitItem
    base.grant(db, world, visit=world['a1'])
    db.get(Visit, world['a1']).currency_snapshot = 'CZK'
    db.get(Visit, world['a1']).price = 0
    db.query(VisitItem).filter_by(visit_id=world['a1']).update({'price': Decimal('NaN')})
    db.commit()
    result = views().get_visit(db, world['one'], world['a1'])
    assert result.price.amount == Decimal('0') and result.price.currency == 'CZK'
    assert result.items[0].price is None


@pytest.mark.parametrize('value, expected', [('Europe/Prague', 'Europe/Prague'), ('UTC', 'UTC'),
                                          ('Broken/Nowhere', None), ('', None), ('/etc/passwd', None)])
def test_current_timezone_is_validated_without_a_fictional_utc_fallback(db, world, value, expected):
    base.grant(db, world, visit=world['a1'])
    db.get(Business, world['a']).timezone = value
    db.commit()
    result = views().get_visit(db, world['one'], world['a1'])
    assert result.business.timezone == expected
    assert result.model_dump(mode='json')['start'].endswith('Z')


def test_nullable_parent_fields_are_not_filled_from_an_item_or_service(db, world):
    base.grant(db, world, visit=world['a1'])
    db.execute(update(Visit).where(Visit.id == world['a1']).values(date_end=None, status=None, service_name=None))
    db.commit()
    result = views().get_visit(db, world['one'], world['a1'])
    assert result.end is None and result.status is None and result.service_name is None
    assert result.items and result.items[0].end is not None


def test_soft_deleted_staff_is_optional_public_name_not_history_authority(db, world):
    from app.db.models.user import User
    base.grant(db, world)
    db.get(User, world['staff_a']).deleted_at = base.NOW
    db.commit()
    page = views().list_visits(db, world['one'])
    assert {entry.id for entry in page.entries} == {world['a1'], world['a2']}
    assert all(entry.staff_name is None and all(item.staff_name is None for item in entry.items)
               for entry in page.entries)


def test_current_staff_business_owner_and_contact_email_do_not_expand_history(db, world):
    from sqlalchemy import delete
    from app.db.models.user import User
    from app.db.models.client import Client
    from app.db.models.client_business_relation import ClientBusinessRelation
    base.grant(db, world)
    db.execute(update(User).where(User.id == world['staff_a']).values(business_id=world['b'],
                email='private-staff-canary@example.com', phone='private-staff-phone'))
    db.execute(update(Client).where(Client.id == world['client']).values(
        owner_id=world['staff_b'], contact_email='other@example.com', full_name='private-client-name'))
    db.execute(delete(ClientBusinessRelation).where(ClientBusinessRelation.client_id == world['client']))
    db.commit()
    page = views().list_visits(db, world['one'])
    assert [entry.id for entry in page.entries] == [world['a1'], world['a2']]
    assert views().get_visit(db, world['one'], world['b1']) is None
    assert views().list_visits(db, world['two']).entries == ()
    assert 'private-' not in page.model_dump_json()


def test_over_200_equal_time_visits_and_many_items_do_not_duplicate_or_truncate_pages(db, world):
    base.grant(db, world)
    ids = [base.add_visit(db, world, start=base.NOW + timedelta(days=10), status='cancelled') for _ in range(205)]
    for visit_id in [ids[-1], ids[-2], ids[0]]:
        _replace_items(db, world, visit_id)
    base.grant(db, world, visit=ids[-1])
    db.commit()
    expected = sorted(ids, reverse=True) + [world['a1'], world['a2']]
    seen, sizes, cursor = [], [], None
    while True:
        page = views().list_visits(db, world['one'], limit=37, cursor=cursor)
        sizes.append(len(page.entries))
        seen.extend(entry.id for entry in page.entries)
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
    assert seen == expected and len(seen) == len(set(seen)) == 207
    assert sizes == [37, 37, 37, 37, 37, 22]
    assert len(views().get_visit(db, world['one'], ids[0]).items) == 3


@pytest.mark.parametrize('limit', [0, 101, True, 1.5, '25'])
def test_public_page_does_not_bypass_existing_limit_validation(db, world, limit):
    with pytest.raises(ValueError):
        views().list_visits(db, world['one'], limit=limit)


def test_business_filter_foreign_cursor_and_existing_typed_cursor_keep_the_same_scope(db, world):
    from app.api.core.visitor_history import VisitorHistoryCursor
    base.grant(db, world)
    db.commit()
    assert views().list_visits(db, world['one'], business_id=world['b']).entries == ()
    page = views().list_visits(db, world['one'], limit=1)
    cursor = VisitorHistoryCursor(page.next_cursor.date_start, page.next_cursor.id)
    assert [row.id for row in views().list_visits(db, world['one'], cursor=cursor).entries] == [world['a2']]
    assert views().get_visit(db, world['two'], world['a1']) is None
    assert views().get_visit(db, world['one'], 9999999) is None


def test_public_models_are_frozen_and_reject_extra_private_fields(db, world):
    from pydantic import ValidationError
    from app.schemas.visitor_history import VisitorHistoryVisit
    base.grant(db, world, visit=world['a1'])
    db.commit()
    result = views().get_visit(db, world['one'], world['a1'])
    with pytest.raises(ValidationError):
        result.status = 'changed'
    with pytest.raises(ValidationError):
        VisitorHistoryVisit.model_validate({**result.model_dump(), 'client_id': world['client']})
    with pytest.raises(ValidationError):
        VisitorHistoryVisit.model_validate(db.get(Visit, world['a1']))
    assert isinstance(result.items, tuple)


def test_list_and_detail_are_select_only_single_statements_and_do_not_flush_pending_mutations(db, world, monkeypatch):
    from sqlalchemy import event, text
    from app.api.core import booking_manage_access, subscription_access
    from app.api.crud import crud_public_booking
    base.grant(db, world, visit=world['a1'])
    db.commit()
    api = views()
    def forbidden(*args, **kwargs):
        pytest.fail('Public history called billing, legacy access or settings helper')
    for name in ('enforce_business_access', 'enforce_subscription_mutation_access', 'get_subscription_access_state'):
        monkeypatch.setattr(subscription_access, name, forbidden)
    monkeypatch.setattr(booking_manage_access, 'resolve_token', forbidden)
    monkeypatch.setattr(crud_public_booking, 'get_or_create_settings', forbidden)
    monkeypatch.setattr(crud_public_booking, 'get_settings', forbidden)
    db.execute(text('SET TRANSACTION READ ONLY'))
    pending = Business(name='Unrelated pending private change', business_type='salon')
    db.add(pending)
    previous_autoflush = db.autoflush
    db.autoflush = True
    statements = []
    def observe(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
        assert statement.lstrip().upper().startswith('SELECT')
        assert 'FOR UPDATE' not in statement.upper() and 'FOR NO KEY UPDATE' not in statement.upper()
        assert 'subscriptions' not in statement and 'notification_outbox' not in statement
        assert 'users.email' not in statement and 'clients.contact_email' not in statement
    event.listen(db.get_bind(), 'before_cursor_execute', observe)
    try:
        page = api.list_visits(db, world['one'])
        assert len(statements) == 1 and [entry.id for entry in page.entries] == [world['a1']]
        assert api.get_visit(db, world['one'], world['a1']).id == world['a1']
        assert len(statements) == 2
        assert pending.id is None and pending in db.new
    finally:
        event.remove(db.get_bind(), 'before_cursor_execute', observe)
        db.autoflush = previous_autoflush
        db.rollback()


@pytest.mark.parametrize('operation', ['list', 'detail'])
@pytest.mark.parametrize('change', ['revoke', 'retarget'])
def test_projection_and_authorization_share_one_snapshot_during_concurrent_changes(db, world, operation, change):
    from sqlalchemy import event
    from sqlalchemy.orm import Session
    from app.db.models.user import User
    from app.db.models.visitor_access import VisitorGrant
    grant_id = base.grant(db, world, visit=world['a1'])
    db.get(Visit, world['a1']).currency_snapshot = 'CZK'
    db.commit()
    api, engine = views(), db.get_bind()
    main_driver = db.connection().connection.driver_connection
    state = {'fired': False, 'main_sql': []}
    def change_after_select(conn, cursor, statement, parameters, context, executemany):
        if conn.connection.driver_connection is not main_driver:
            return
        state['main_sql'].append(statement)
        if state['fired'] or not statement.lstrip().upper().startswith('SELECT'):
            return
        state['fired'] = True
        with Session(engine) as writer:
            if change == 'revoke':
                writer.execute(update(VisitorGrant).where(VisitorGrant.id == grant_id).values(revoked_at=base.NOW))
            else:
                writer.execute(update(Visit).where(Visit.id == world['a1']).values(client_id=world['other_client']))
            writer.execute(update(User).where(User.id == world['staff_a']).values(full_name='Changed after SQL'))
            writer.commit()
    event.listen(engine, 'after_cursor_execute', change_after_select)
    try:
        result = (api.list_visits(db, world['one']).entries[0] if operation == 'list'
                  else api.get_visit(db, world['one'], world['a1']))
        assert state['fired'] and len(state['main_sql']) == 1
        assert result.id == world['a1'] and result.staff_name == 'Master A'
        assert result.price.amount == Decimal('1234.56') and result.price.currency == 'CZK'
        assert api.get_visit(db, world['one'], world['a1']) is None
        assert len(state['main_sql']) == 2
    finally:
        event.remove(engine, 'after_cursor_execute', change_after_select)
        db.rollback()


def test_replacement_grant_and_business_retarget_do_not_resurrect_public_history(db, world):
    from app.db.models.visitor_access import VisitorGrant
    grant_id = base.grant(db, world, visit=world['a1'])
    db.commit()
    assert views().get_visit(db, world['one'], world['a1']) is not None
    db.execute(update(VisitorGrant).where(VisitorGrant.id == grant_id).values(revoked_at=base.NOW))
    base.grant(db, world, actor='two', visit=world['a1'])
    db.commit()
    assert views().get_visit(db, world['one'], world['a1']) is None
    assert views().get_visit(db, world['two'], world['a1']) is not None
    db.execute(update(Visit).where(Visit.id == world['a1']).values(business_id=world['b']))
    db.commit()
    assert views().get_visit(db, world['two'], world['a1']) is None


def test_public_business_contacts_are_explicit_and_item_dto_has_no_hidden_details(db, world):
    base.grant(db, world, visit=world['a1'])
    business = db.get(Business, world['a'])
    business.address, business.phone, business.email = 'Public street 1', '+420111222333', 'public@example.com'
    db.commit()
    result = views().get_visit(db, world['one'], world['a1'])
    assert result.business.address == 'Public street 1' and result.business.email == 'public@example.com'
    assert result.business.phone == '+420111222333'
    assert set(result.items[0].model_dump()) == {
        'id', 'position', 'service_name', 'start', 'end', 'duration_minutes', 'staff_name', 'price'}
    assert result.model_dump(mode='json')['items'][0]['start'].endswith('Z')
