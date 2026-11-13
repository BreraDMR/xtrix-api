"""Committed visitor mutations must retain all matching work across restarts."""
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.core.released_slot_queue import run_pending
from app.api.core.visitor_cancel import cancel_visit
from app.api.core.visitor_reschedule import reschedule_visit
from app.db.models.released_slot_job import ReleasedSlotJob as Job
from app.db.models.visit import Visit
from tests import test_released_chain_slots as chain

@pytest.fixture
def world(db, monkeypatch):
    value = chain.world.__wrapped__(db, monkeypatch)
    from app.api.core import released_slot_queue
    monkeypatch.setattr(released_slot_queue, 'utcnow', lambda: value['clock'][0])
    return value


@pytest.mark.parametrize('action', ['cancel', 'reschedule'])
def test_visitor_commit_keeps_each_released_segment_for_a_later_process(db, world, action):
    if action == 'cancel':
        cancel_visit(db, world['raw'], world['visit'])
    else:
        reschedule_visit(db, world['raw'], world['visit'], new_start=world['new_start'])
    db.commit()
    with Session(db.get_bind()) as restarted:
        jobs = restarted.execute(select(Job).order_by(Job.segment)).scalars().all()
        assert [(job.service_id, job.specialist_id, job.start) for job in jobs] == world['slots']
        assert all(job.status == 'pending' for job in jobs)


def test_retry_after_a_failed_matcher_does_not_undo_the_saved_cancellation(db, world):
    cancel_visit(db, world['raw'], world['visit'])
    db.commit()
    calls = []
    def fail(*args):
        raise RuntimeError('PRIVATE_TRANSPORT_SENTINEL')
    first = run_pending(db, matcher=fail)
    assert first['retried'] == 2
    jobs = db.query(Job).order_by(Job.segment).all()
    assert all(job.status == 'pending' and job.attempts == 1 and job.last_error == 'matching_failed' for job in jobs)
    retry_at = max(job.next_attempt_at for job in jobs)
    assert db.get(Visit, world['visit']).status == 'cancelled'
    db.rollback()
    with Session(db.get_bind()) as restarted:
        second = run_pending(restarted, matcher=lambda *args: calls.append(args[2:]), now=retry_at)
        assert second['done'] == 2
        assert calls == world['slots']


def test_rollback_leaves_neither_cancellation_nor_released_slot_jobs(db, world):
    cancel_visit(db, world['raw'], world['visit'])
    assert db.query(Job).count() == 2
    db.rollback()
    assert db.query(Job).count() == 0
    assert db.get(Visit, world['visit']).status == 'planned'


def test_repeat_cancel_does_not_create_another_release_event(db, world):
    cancel_visit(db, world['raw'], world['visit'])
    db.commit()
    cancel_visit(db, world['raw'], world['visit'])
    db.commit()
    assert db.query(Job).count() == 2


def test_expired_slots_are_not_matched(db, world):
    cancel_visit(db, world['raw'], world['visit'])
    db.commit()
    def forbidden(*args):
        pytest.fail('An already started slot must never be matched')
    result = run_pending(db, now=max(start for _, _, start in world['slots']) + timedelta(seconds=1), matcher=forbidden)
    assert result['expired'] == 2


def test_matcher_writes_and_acknowledgement_rollback_together_on_commit_failure(db, world):
    from sqlalchemy import text
    from app.db.models.audit_log import AuditLog
    cancel_visit(db, world['raw'], world['visit'])
    db.commit()
    db.execute(text("""CREATE FUNCTION reject_slot_done() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Synthetic deferred acknowledgement failure'; END $$"""))
    db.execute(text("""CREATE CONSTRAINT TRIGGER reject_slot_done AFTER UPDATE ON released_slot_jobs
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.status = 'done')
        EXECUTE FUNCTION reject_slot_done()"""))
    db.commit()
    def write_before_failure(session, *_args):
        session.add(AuditLog(entity_type='released_slot_probe', entity_id=0, action='probe', changes={}))
    try:
        stats = run_pending(db, limit=1, matcher=write_before_failure)
        assert stats['retried'] == 1
        assert db.query(AuditLog).filter_by(action='probe').count() == 0
        assert db.query(Job).filter_by(status='done').count() == 0
        assert db.query(Job).filter_by(last_error='matching_failed').count() == 1
    finally:
        db.rollback()
        db.execute(text('DROP TRIGGER reject_slot_done ON released_slot_jobs'))
        db.execute(text('DROP FUNCTION reject_slot_done()'))
        db.commit()


def test_two_runners_skip_the_same_locked_job(db, world):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    cancel_visit(db, world['raw'], world['visit'])
    db.commit()
    first_locked, release = Event(), Event()
    calls = []
    def hold(session, business, service, staff, start):
        calls.append((service, staff, start))
        first_locked.set()
        assert release.wait(10)
    def first():
        with Session(db.get_bind()) as session:
            return run_pending(session, limit=1, matcher=hold)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(first)
        try:
            assert first_locked.wait(10)
            with Session(db.get_bind()) as other:
                second = run_pending(other, limit=1, matcher=lambda _db, _business, *slot: calls.append(slot))
            assert second['done'] == 1
        finally:
            release.set()
        assert future.result(timeout=10)['done'] == 1
    assert len(calls) == len(set(calls)) == 2
    assert db.query(Job).filter_by(status='done').count() == 2


def test_deleted_business_cancels_work_without_matching(db, world):
    from app.db.models.business import Business
    cancel_visit(db, world['raw'], world['visit'])
    db.get(Business, world['business']).deleted_at = world['clock'][0]
    db.commit()
    def forbidden(*args):
        pytest.fail('A deleted salon must not receive new offers')
    assert run_pending(db, matcher=forbidden)['cancelled'] == 2


def test_final_authority_failure_rolls_back_the_enqueued_work(db, world):
    from sqlalchemy import event
    from app.api.core import visitor_action_target as targets
    def expire_proof_after_enqueue(_connection, _cursor, sql, *_args):
        if sql.startswith('INSERT INTO released_slot_jobs'):
            world['clock'][0] += timedelta(days=31)
    event.listen(db.get_bind(), 'after_cursor_execute', expire_proof_after_enqueue)
    try:
        with pytest.raises(targets.InvalidActionSession):
            cancel_visit(db, world['raw'], world['visit'])
    finally:
        event.remove(db.get_bind(), 'after_cursor_execute', expire_proof_after_enqueue)
    db.rollback()
    assert db.query(Job).count() == 0
    assert db.get(Visit, world['visit']).status == 'planned'


def test_migration_guard_preserves_pending_jobs(db, world):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    cancel_visit(db, world['raw'], world['visit'])
    db.commit()
    db.close()
    with pytest.raises(DBAPIError, match='Released-slot jobs exist'):
        command.downgrade(Config('alembic.ini'), '0088_financial_archives')
    assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '0090_visit_price_corrections'
    assert db.query(Job).count() == 2
    db.rollback()


def test_empty_migration_roundtrip_does_not_backfill_old_cancellations(db, world):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect
    db.get(Visit, world['visit']).status = 'cancelled'
    db.commit()
    db.close()
    config = Config('alembic.ini')
    try:
        command.downgrade(config, '0088_financial_archives')
        assert not inspect(db.get_bind()).has_table('released_slot_jobs')
        command.upgrade(config, 'head')
        assert db.query(Job).count() == 0
    finally:
        db.rollback()
        db.close()
        command.upgrade(config, 'head')
