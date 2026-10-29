"""Visitor cancellation commits the visit, unpaid holds and notifications together."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event

import pytest
from sqlalchemy import event, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.core import notification_events, notification_preferences, subscription_access
from app.api.core import visitor_action_target as targets
from app.api.core.booking_manage_policy import BookingActionNotAllowed
from app.api.core.visitor_cancel import cancel_visit
from app.db.models.client import Client
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.notification_settings import NotificationSettings
from app.db.models.service_payment import ServicePaymentAttempt
from app.db.models.subscription import Subscription
from app.db.models.visit import Visit
from app.db.models.visit_event import VisitEvent
from app.db.models.visitor_access import VisitorGrant
from tests import test_visitor_confirm_core as base
from tests import test_visitor_action_target as locks
from tests.test_visitor_pending_payments import add_attempt


@pytest.fixture
def world(db, monkeypatch):
    result = base.world.__wrapped__(db, monkeypatch)
    monkeypatch.setattr(subscription_access, 'utcnow', lambda: result['clock'][0])
    monkeypatch.setattr(notification_preferences, 'channel_ready', lambda channel: channel == 'email')
    person = db.get(Client, result['client'])
    person.contact_email = 'synthetic-customer@example.test'
    person.language = 'ru'
    db.add(NotificationSettings(business_id=result['business'], is_enabled=True, channel='email'))
    result['pending'] = add_attempt(db, result, 'pending')
    result['paid'] = add_attempt(db, result, 'paid', 'paid')
    db.add(NotificationOutbox(business_id=result['business'], visit_id=result['visit'],
        event_key='synthetic-old-reminder', kind='booking_reminder', channel='email',
        recipient=person.contact_email, payload={}, status='pending'))
    db.commit()
    return result


def state(db, world):
    return dict(visit=base.snapshot(db, world),
        attempts=[dict(row) for row in db.execute(select(ServicePaymentAttempt.__table__).order_by(
            ServicePaymentAttempt.id)).mappings()],
        events=[dict(row) for row in db.execute(select(VisitEvent.__table__)).mappings()],
        outbox=[dict(row) for row in db.execute(select(NotificationOutbox.__table__).order_by(
            NotificationOutbox.id)).mappings()])


def test_atomic_cancel_preserves_quotes_paid_attempt_and_repeats_without_duplicates(db, world):
    before = state(db, world)
    db.rollback()
    result = cancel_visit(db, world['raw'], world['visit'])
    assert result.id == world['visit'] and result.changed
    assert result.business_id == world['business'] and result.specialist_id == world['staff']
    after = state(db, world)
    assert {key for key in before['visit'][0] if before['visit'][0][key] != after['visit'][0][key]} == {'status'}
    assert after['visit'][0]['status'] == 'cancelled'
    for old, new in zip(before['visit'][1], after['visit'][1]):
        assert {key: value for key, value in old.items() if key != 'active'} == {
            key: value for key, value in new.items() if key != 'active'}
        assert not new['active']
    assert db.get(ServicePaymentAttempt, world['pending']).status == 'cancelled'
    assert next(row for row in before['attempts'] if row['id'] == world['paid']) == next(
        row for row in after['attempts'] if row['id'] == world['paid'])
    assert len(after['events']) == 1 and after['events'][0]['event_type'] == 'cancelled'
    assert [row['status'] for row in after['outbox'] if row['kind'] == 'booking_reminder'] == ['cancelled']
    messages = [row for row in after['outbox'] if row['kind'] == 'booking_cancelled']
    assert len(messages) == 1 and messages[0]['recipient'] == 'synthetic-customer@example.test'
    with Session(db.get_bind()) as observer:
        assert state(observer, world) == before
    db.commit()
    assert not cancel_visit(db, world['same_raw'], world['visit']).changed
    assert state(db, world) == after
    db.commit()


def test_rollback_keeps_original_visit_payments_event_and_reminder(db, world):
    before = state(db, world)
    db.rollback()
    assert cancel_visit(db, world['raw'], world['visit']).changed
    db.rollback()
    assert state(db, world) == before


@pytest.mark.parametrize('change', ['foreign', 'revoked', 'retargeted', 'expired', 'unpaid', 'completed', 'late'])
def test_refusal_does_not_change_any_cancellation_state(db, world, change):
    raw = world['raw']
    error = BookingActionNotAllowed
    if change == 'foreign':
        raw = world['other_raw']
        error = targets.ActionTargetUnavailable
    elif change == 'revoked':
        db.get(VisitorGrant, world['grant']).revoked_at = world['clock'][0]
        error = targets.ActionTargetUnavailable
    elif change == 'retargeted':
        db.get(Visit, world['visit']).client_id = world['other_client']
        error = targets.ActionTargetUnavailable
    elif change == 'expired':
        world['clock'][0] += timedelta(days=31)
        error = targets.InvalidActionSession
    elif change == 'unpaid':
        db.get(Subscription, world['sub']).status = 'cancelled'
    elif change == 'completed':
        db.get(Visit, world['visit']).status = 'completed'
    else:
        world['clock'][0] += timedelta(days=2, microseconds=1)
    db.commit()
    before = state(db, world)
    db.rollback()
    with pytest.raises(error):
        cancel_visit(db, raw, world['visit'])
    db.rollback()
    assert state(db, world) == before


def test_late_cutoff_after_real_cancel_update_rolls_back_holds_and_notifications(db, world):
    before = state(db, world)
    db.rollback()
    updates = []
    def after(_conn, _cursor, statement, *args):
        if statement.startswith('UPDATE visits SET status'):
            updates.append(statement)
            world['clock'][0] += timedelta(days=2, microseconds=1)
    event.listen(db.get_bind(), 'after_cursor_execute', after)
    try:
        with pytest.raises(BookingActionNotAllowed) as exc:
            cancel_visit(db, world['raw'], world['visit'])
    finally:
        event.remove(db.get_bind(), 'after_cursor_execute', after)
    assert updates and exc.value.reason == 'booking_cancel_too_late'
    db.rollback()
    assert state(db, world) == before


def test_deferred_commit_failure_does_not_save_cancellation_or_notification(db, world):
    db.execute(text("""CREATE FUNCTION reject_cancel_event() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'synthetic deferred cancellation failure'; RETURN NEW; END $$"""))
    db.execute(text('''CREATE CONSTRAINT TRIGGER reject_cancel_event AFTER INSERT ON visit_events
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION reject_cancel_event()'''))
    db.commit()
    before = state(db, world)
    db.rollback()
    try:
        assert cancel_visit(db, world['raw'], world['visit']).changed
        with pytest.raises(DBAPIError):
            db.commit()
        db.rollback()
        assert state(db, world) == before
    finally:
        db.rollback()
        db.execute(text('DROP TRIGGER reject_cancel_event ON visit_events'))
        db.execute(text('DROP FUNCTION reject_cancel_event()'))
        db.commit()


def test_unrelated_pending_client_is_not_flushed(db, world):
    pending = Client(full_name='Unrelated pending caller object')
    db.add(pending)
    assert cancel_visit(db, world['raw'], world['visit']).changed
    assert pending.id is None and pending in db.new
    db.rollback()


def test_existing_cancellation_still_requires_current_business_access(db, world):
    assert cancel_visit(db, world['raw'], world['visit']).changed
    db.commit()
    db.get(Subscription, world['sub']).status = 'cancelled'
    db.commit()
    before = state(db, world)
    db.rollback()
    with pytest.raises(BookingActionNotAllowed) as exc:
        cancel_visit(db, world['raw'], world['visit'])
    assert exc.value.reason == 'visitor_actions_unavailable'
    db.rollback()
    assert state(db, world) == before


def test_past_unpaid_hold_can_be_released_without_refunding_paid_history(db, world):
    visit = db.get(Visit, world['visit'])
    visit.status = 'awaiting_payment'
    visit.date_start = world['clock'][0] - timedelta(hours=1)
    visit.date_end = world['clock'][0]
    db.commit()
    assert cancel_visit(db, world['raw'], world['visit']).changed
    db.commit()
    assert db.get(ServicePaymentAttempt, world['pending']).status == 'cancelled'
    assert db.get(ServicePaymentAttempt, world['paid']).status == 'paid'


def test_latest_notification_preference_is_used_even_if_caller_cached_it(db, world):
    cached = db.query(NotificationSettings).filter_by(business_id=world['business']).one()
    assert cached.is_enabled
    with Session(db.get_bind()) as writer:
        writer.execute(update(NotificationSettings).where(NotificationSettings.id == cached.id)
                       .values(is_enabled=False))
        writer.commit()
    assert cancel_visit(db, world['raw'], world['visit']).changed
    assert db.query(NotificationOutbox).filter_by(kind='booking_cancelled').count() == 0
    db.rollback()


def test_concurrent_sessions_cancel_once_and_queue_one_event(db, world):
    first = cancel_visit(db, world['raw'], world['visit'])
    assert first.changed
    started = Event()
    state_holder = {}
    engine = db.get_bind()
    def repeat():
        with Session(engine) as second:
            state_holder['pid'] = second.execute(text('SELECT pg_backend_pid()')).scalar_one()
            started.set()
            result = cancel_visit(second, world['same_raw'], world['visit'])
            second.commit()
            return result
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(repeat)
        try:
            assert started.wait(5)
            assert locks.wait_blocked(engine, state_holder['pid'])
        finally:
            db.commit()
        assert not future.result(timeout=5).changed
    assert db.query(VisitEvent).filter_by(visit_id=world['visit']).count() == 1
    assert db.query(NotificationOutbox).filter_by(visit_id=world['visit'], kind='booking_cancelled').count() == 1


def test_notification_failure_requires_whole_rollback_of_every_side_effect(db, world, monkeypatch):
    before = state(db, world)
    db.rollback()
    original = notification_events.queue_booking_cancelled
    def fail_after_enqueue(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('synthetic failure after enqueue')
    monkeypatch.setattr(notification_events, 'queue_booking_cancelled', fail_after_enqueue)
    with pytest.raises(RuntimeError):
        cancel_visit(db, world['raw'], world['visit'])
    db.rollback()
    assert state(db, world) == before


def test_pending_preference_edit_is_not_refreshed_or_flushed(db, world):
    settings = db.query(NotificationSettings).filter_by(business_id=world['business']).one()
    settings.is_enabled = False
    with pytest.raises(targets.DirtyActionState):
        cancel_visit(db, world['raw'], world['visit'])
    assert settings.is_enabled is False and settings in db.dirty
    db.rollback()
    assert db.get(ServicePaymentAttempt, world['pending']).status == 'pending'
    assert db.get(Visit, world['visit']).status == 'planned'
