"""Retry only local waitlist matching; no transport, global worker or auto booking."""
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.api.core.clock import utcnow
from app.db.models.business import Business
from app.db.models.released_slot_job import ReleasedSlotJob as Job


def enqueue_slots(db, business_id, slots, *, event_key):
    """The caller owns the booking transaction, including any enqueue failure."""
    if business_id is None:
        return
    if not isinstance(event_key, str) or not 1 <= len(event_key) <= 120:
        raise ValueError('A bounded release event key is required')
    now = utcnow()
    for segment, slot in enumerate(dict.fromkeys(slots)):
        if slot.service_id is None:
            continue
        db.execute(insert(Job).values(
            business_id=business_id, event_key=event_key, segment=segment,
            service_id=slot.service_id, specialist_id=slot.specialist_id,
            start=slot.start, end=slot.end, status='pending', attempts=0,
            next_attempt_at=now, created_at=now, updated_at=now,
        ).on_conflict_do_nothing(index_elements=['event_key', 'segment']))


def _retry(db, job_id, old_attempts, old_due, now):
    job = db.execute(select(Job).where(Job.id == job_id, Job.status == 'pending',
        Job.attempts == old_attempts, Job.next_attempt_at == old_due)
        .with_for_update(skip_locked=True)).scalar_one_or_none()
    if job is None:
        db.rollback()
        return
    job.attempts += 1
    job.updated_at = now
    job.last_error = 'matching_failed'
    if now >= job.start:
        job.status, job.next_attempt_at = 'expired', None
    else:
        delay = min(300, 30 * 2 ** min(job.attempts - 1, 4))
        job.next_attempt_at = min(job.start, now + timedelta(seconds=delay))
    db.commit()


def run_pending(db, *, limit=100, now=None, matcher=None):
    """Each job, generated offers and notification rows commit together.

    A process crash rolls back the row lock and all matching work. Explicit calls
    only: this runner is deliberately not connected to the global scheduler.
    """
    if db.new or db.dirty or db.deleted:
        raise ValueError('Save pending changes before running released-slot jobs')
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError('A batch must contain between 1 and 500 jobs')
    if matcher is None:
        from app.api.core.waitlist_service import match_released_slot
        matcher = match_released_slot
    stats = {'done': 0, 'retried': 0, 'expired': 0, 'cancelled': 0}
    for _ in range(limit):
        due = now or utcnow()
        job = db.execute(select(Job).where(Job.status == 'pending', Job.next_attempt_at <= due)
            .order_by(Job.attempts, Job.next_attempt_at, Job.id)
            .with_for_update(skip_locked=True).limit(1)).scalar_one_or_none()
        if job is None:
            db.rollback()
            break
        job_id, old_attempts, old_due = job.id, job.attempts, job.next_attempt_at
        checked = now or utcnow()
        try:
            business = db.get(Business, job.business_id)
            if checked >= job.start:
                state = 'expired'
            elif business is None or business.deleted_at is not None:
                state = 'cancelled'
            else:
                matcher(db, business, job.service_id, job.specialist_id, job.start)
                state = 'done'
            job.status, job.next_attempt_at = state, None
            job.attempts += 1
            job.updated_at, job.last_error = checked, None
            db.commit()
            stats[state] += 1
        except Exception:
            db.rollback()
            _retry(db, job_id, old_attempts, old_due, now or utcnow())
            stats['retried'] += 1
    return stats
