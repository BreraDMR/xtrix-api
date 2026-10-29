"""Cancelling unpaid holds never waits behind an attempt-first payment writer."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Event

import pytest
from sqlalchemy import event, inspect, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.core import visitor_action_target as targets
from app.api.core import visitor_pending_payments as payments
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.service_payment import ServicePaymentAttempt
from tests import test_visitor_action_target as base

world = base.world


def add_attempt(db, world, key, status='pending', business_id=None):
    row = ServicePaymentAttempt(business_id=business_id or world['business'], visit_id=world['visit'],
        provider='synthetic', merchant='synthetic', request_key=key, public_ref=key,
        public_token_hash='0' * 64, status=status, currency='CZK', amount=Decimal('12.30'),
        remaining_amount=Decimal('987.70'), refunded_amount=0,
        expires_at=base.NOW + timedelta(hours=1), created_at=base.NOW, updated_at=base.NOW)
    db.add(row)
    db.flush()
    return row.id


def snapshot(db):
    return [dict(row) for row in db.execute(select(ServicePaymentAttempt.__table__).order_by(
        ServicePaymentAttempt.id)).mappings()]


def test_all_pending_only_status_and_timestamp_change_in_caller_transaction(db, world):
    first = add_attempt(db, world, 'first')
    second = add_attempt(db, world, 'second')
    add_attempt(db, world, 'paid', 'paid')
    add_attempt(db, world, 'refunded', 'refunded')
    db.commit()
    before = snapshot(db)
    db.rollback()
    world['clock'][0] += timedelta(minutes=1)
    target = targets.resolve_action_target(db, world['raw'], world['visit'])
    assert payments.cancel_pending_payments(db, world['raw'], target) == (first, second)
    after = snapshot(db)
    for old, new in zip(before, after):
        if old['id'] in (first, second):
            assert {key for key in old if old[key] != new[key]} == {'status', 'updated_at'}
            assert new['status'] == 'cancelled'
        else:
            assert new == old
    assert payments.cancel_pending_payments(db, world['raw'], target) == ()
    with Session(db.get_bind()) as observer:
        assert snapshot(observer) == before
    db.commit()
    with Session(db.get_bind()) as observer:
        assert snapshot(observer) == after


def test_whole_rollback_keeps_pending_and_all_financial_fields(db, world):
    add_attempt(db, world, 'rollback')
    db.commit()
    before = snapshot(db)
    db.rollback()
    target = targets.resolve_action_target(db, world['raw'], world['visit'])
    assert payments.cancel_pending_payments(db, world['raw'], target)
    db.rollback()
    assert snapshot(db) == before


def test_attempt_first_writer_causes_nowait_conflict_and_account_is_released(db, world):
    attempt_id = add_attempt(db, world, 'busy')
    db.commit()
    with Session(db.get_bind()) as blocker:
        blocker.execute(select(ServicePaymentAttempt.id).where(ServicePaymentAttempt.id == attempt_id)
                        .with_for_update())
        target = targets.resolve_action_target(db, world['raw'], world['visit'])
        with pytest.raises(targets.ActionBusy):
            payments.cancel_pending_payments(db, world['raw'], target)
        with pytest.raises(DBAPIError) as failed:
            db.execute(text('SELECT 1'))
        assert failed.value.orig.pgcode == '25P02'
        db.rollback()
        with Session(db.get_bind()) as observer:
            observer.execute(text('SELECT id FROM visitor_accounts WHERE id=:id FOR UPDATE NOWAIT'),
                             {'id': world['account']})
            observer.execute(text('SELECT id FROM visits WHERE id=:id FOR UPDATE NOWAIT'),
                             {'id': world['visit']})
    assert db.get(ServicePaymentAttempt, attempt_id).status == 'pending'


def test_cross_business_attempt_fails_closed_without_partial_cancellation(db, world):
    add_attempt(db, world, 'ours')
    other = Business(name='Other payment business', business_type='salon')
    db.add(other)
    db.flush()
    add_attempt(db, world, 'foreign', business_id=other.id)
    db.commit()
    before = snapshot(db)
    db.rollback()
    target = targets.resolve_action_target(db, world['raw'], world['visit'])
    with pytest.raises(payments.PendingPaymentConflict):
        payments.cancel_pending_payments(db, world['raw'], target)
    db.rollback()
    assert snapshot(db) == before


def test_no_pending_does_not_flush_unrelated_client_or_refresh_cached_attempt(db, world):
    attempt_id = add_attempt(db, world, 'closed', status='paid')
    db.commit()
    cached = db.get(ServicePaymentAttempt, attempt_id)
    pending = Client(full_name='Unrelated pending client')
    db.add(pending)
    target = targets.resolve_action_target(db, world['raw'], world['visit'])
    assert payments.cancel_pending_payments(db, world['raw'], target) == ()
    assert pending.id is None and pending in db.new
    assert not inspect(cached).expired and cached.status == 'paid'
    db.rollback()


def test_dirty_payment_is_rejected_without_refresh_or_flush(db, world):
    attempt_id = add_attempt(db, world, 'dirty')
    db.commit()
    row = db.get(ServicePaymentAttempt, attempt_id)
    row.amount = Decimal('99.99')
    target = targets.resolve_action_target(db, world['raw'], world['visit'])
    with pytest.raises(targets.DirtyActionState):
        payments.cancel_pending_payments(db, world['raw'], target)
    assert row.amount == Decimal('99.99') and row in db.dirty
    db.rollback()
    assert db.get(ServicePaymentAttempt, attempt_id).amount == Decimal('12.30')


def test_expiry_after_real_update_requires_whole_rollback(db, world):
    attempt_id = add_attempt(db, world, 'expiry')
    db.commit()
    target = targets.resolve_action_target(db, world['raw'], world['visit'])
    updates = []
    def after(_conn, _cursor, statement, *args):
        if statement.startswith('UPDATE service_payment_attempts SET'):
            updates.append(statement)
            world['clock'][0] += timedelta(days=31)
    event.listen(db.get_bind(), 'after_cursor_execute', after)
    try:
        with pytest.raises(targets.InvalidActionSession):
            payments.cancel_pending_payments(db, world['raw'], target)
    finally:
        event.remove(db.get_bind(), 'after_cursor_execute', after)
    assert updates
    db.rollback()
    assert db.get(ServicePaymentAttempt, attempt_id).status == 'pending'


def test_ended_savepoint_facts_cannot_cancel_holds(db, world):
    attempt_id = add_attempt(db, world, 'savepoint')
    db.commit()
    nested = db.begin_nested()
    target = targets.resolve_action_target(db, world['raw'], world['visit'])
    nested.rollback()
    with pytest.raises(targets.DirtyActionState):
        payments.cancel_pending_payments(db, world['raw'], target)
    assert db.get(ServicePaymentAttempt, attempt_id).status == 'pending'


def test_attempt_to_visit_writer_finishes_after_cancel_conflict_rollback(db, world):
    attempt_id = add_attempt(db, world, 'writer-cycle')
    db.commit()
    target = targets.resolve_action_target(db, world['raw'], world['visit'])
    locked = Event()
    state = {}
    engine = db.get_bind()
    def payment_writer():
        with Session(engine) as writer:
            state['pid'] = writer.execute(text('SELECT pg_backend_pid()')).scalar_one()
            writer.execute(text("UPDATE service_payment_attempts SET status='paid' WHERE id=:id"),
                           {'id': attempt_id})
            locked.set()
            writer.execute(text('UPDATE visits SET client_confirmed_at=:now WHERE id=:id'),
                           {'now': base.NOW, 'id': world['visit']})
            writer.commit()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(payment_writer)
        try:
            assert locked.wait(5)
            assert base.wait_blocked(engine, state['pid'])
            with pytest.raises(targets.ActionBusy):
                payments.cancel_pending_payments(db, world['raw'], target)
        finally:
            db.rollback()
        future.result(timeout=5)
    assert db.get(ServicePaymentAttempt, attempt_id).status == 'paid'
