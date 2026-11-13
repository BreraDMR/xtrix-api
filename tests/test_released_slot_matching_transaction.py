"""Matching and its notification rows share the durable job transaction."""
from uuid import uuid4

from app.api.core.released_slot_queue import enqueue_slots, run_pending
from app.api.core.released_slots import ReleasedSlot
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.waitlist import WaitlistOffer
from tests import test_waitlist as waitlist

salon = waitlist.salon


def test_default_local_matcher_persists_one_offer_and_one_notification_with_job_ack(client, db, salon):
    assert waitlist._join(client, salon).status_code == 202
    enqueue_slots(db, salon['business']['id'], [ReleasedSlot(salon['service']['id'], salon['owner'].id, salon['start'])],
                  event_key='probe:' + uuid4().hex)
    db.commit()
    assert run_pending(db)['done'] == 1
    assert db.query(WaitlistOffer).count() == 1
    assert db.query(NotificationOutbox).filter_by(kind='waitlist_offer').count() == 1
    assert run_pending(db)['done'] == 0
    assert db.query(WaitlistOffer).count() == 1


def test_default_matcher_does_not_commit_an_offer_before_the_job_ack(client, db, salon):
    from sqlalchemy import text
    assert waitlist._join(client, salon).status_code == 202
    enqueue_slots(db, salon['business']['id'], [ReleasedSlot(salon['service']['id'], salon['owner'].id, salon['start'])],
                  event_key='probe:' + uuid4().hex)
    db.execute(text("""CREATE FUNCTION reject_real_slot_done() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Synthetic deferred failure'; END $$"""))
    db.execute(text("""CREATE CONSTRAINT TRIGGER reject_real_slot_done AFTER UPDATE ON released_slot_jobs
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.status = 'done')
        EXECUTE FUNCTION reject_real_slot_done()"""))
    db.commit()
    try:
        assert run_pending(db, limit=1)['retried'] == 1
        assert db.query(WaitlistOffer).count() == 0
        assert db.query(NotificationOutbox).filter_by(kind='waitlist_offer').count() == 0
    finally:
        db.rollback()
        db.execute(text('DROP TRIGGER reject_real_slot_done ON released_slot_jobs'))
        db.execute(text('DROP FUNCTION reject_real_slot_done()'))
        db.commit()
    from app.db.models.released_slot_job import ReleasedSlotJob
    retry_at = db.query(ReleasedSlotJob).one().next_attempt_at
    db.rollback()
    assert run_pending(db, now=retry_at)['done'] == 1
    assert db.query(WaitlistOffer).count() == 1
    assert db.query(NotificationOutbox).filter_by(kind='waitlist_offer').count() == 1
