"""Taking a deleted visit's slots back from the released-slot queue and the waitlist."""
from datetime import timedelta

from sqlalchemy import select

from app.api.core import released_slots
from app.api.core.clock import utcnow
from app.db.models.released_slot_job import ReleasedSlotJob as Job
from app.db.models.waitlist import WaitlistOffer


class SlotOffered(Exception):
    """Somebody holds a live waitlist offer for this time; let them answer first."""


def _offered(db, business_id, slot, now):
    end = slot.end or slot.start + timedelta(hours=1)
    return db.execute(select(WaitlistOffer.id).where(
        WaitlistOffer.business_id == business_id,
        WaitlistOffer.specialist_id == slot.specialist_id,
        WaitlistOffer.status == 'pending',
        WaitlistOffer.expires_at > now,
        WaitlistOffer.start < end,
        WaitlistOffer.end > slot.start,
    ).limit(1)).first() is not None


def reclaim_slots(db, visit):
    """Call inside the restore transaction, before deleted_at goes back to None.

    The caller commits or rolls back. A cancelled visit gave nothing to the queue
    on delete and takes nothing back, so it is left alone.
    """
    if visit.business_id is None or visit.status == 'cancelled':
        return
    # The worker keeps a job row locked while it matches, so waiting here means
    # any offers it was about to make are already visible to the check below.
    jobs = db.execute(select(Job).where(
        Job.business_id == visit.business_id,
        Job.event_key.like(f'visit:{visit.id}:deleted:%'),
        Job.status == 'pending',
    ).order_by(Job.id).with_for_update()).scalars().all()
    now = utcnow()
    if any(_offered(db, visit.business_id, slot, now) for slot in released_slots.visit_slots(visit)):
        raise SlotOffered()
    for job in jobs:
        job.status, job.next_attempt_at, job.updated_at = 'cancelled', None, now
