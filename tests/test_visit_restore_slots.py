"""Bringing a deleted visit back must not leave the waitlist chasing its slot.

Deleting a visit queues its freed slots and may already have offered them. A restore
takes the slot back, so the queue work for that delete is cancelled in the same commit,
and a restore while somebody holds a live offer for that time is refused with 409.
"""
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.core import released_slots
from app.api.core.clock import utcnow
from app.api.core.released_slot_queue import run_pending
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.released_slot_job import ReleasedSlotJob as Job
from app.db.models.visit import Visit
from app.db.models.waitlist import WaitlistOffer
from tests import test_waitlist as wl

salon = wl.salon


def _jobs(db):
    with Session(db.get_bind()) as fresh:
        return [job.status for job in fresh.execute(select(Job).order_by(Job.id)).scalars()]


def _offer_mail(db):
    db.expire_all()
    return db.query(NotificationOutbox).filter(NotificationOutbox.kind == 'waitlist_offer').count()


def _deleted(db, visit_id):
    db.expire_all()
    return db.get(Visit, visit_id).deleted_at is not None


def _restore(client, salon, visit_id):
    return client.post(f'/visits/{visit_id}/restore', headers=salon['headers'])


def test_delete_restore_then_queue_offers_nothing(client, db, salon, monkeypatch):
    assert wl._join(client, salon).status_code == 202
    # inline matching right after the delete is down, so only the queue knows the slot
    monkeypatch.setattr(released_slots, 'match_quietly', lambda *_args, **_kw: None)
    visit = wl._visit(client, salon)
    assert client.delete(f"/visits/{visit['id']}", headers=salon['headers']).status_code == 200
    assert _jobs(db) == ['pending']

    restored = _restore(client, salon, visit['id'])
    assert restored.status_code == 200, restored.text
    assert _jobs(db) == ['cancelled']

    stats = run_pending(db, now=utcnow())
    assert stats['done'] == 0
    assert wl._offer_rows(db, salon['business']['id']) == []
    assert _offer_mail(db) == 0


def test_queue_finds_the_slot_busy_even_if_a_job_slipped_through(client, db, salon, monkeypatch):
    # belt and braces: a job the restore didn't see still can't offer a taken slot
    assert wl._join(client, salon).status_code == 202
    monkeypatch.setattr(released_slots, 'match_quietly', lambda *_args, **_kw: None)
    visit = wl._visit(client, salon)
    assert client.delete(f"/visits/{visit['id']}", headers=salon['headers']).status_code == 200
    assert _restore(client, salon, visit['id']).status_code == 200
    db.query(Job).update({Job.status: 'pending', Job.next_attempt_at: utcnow()})
    db.commit()

    assert run_pending(db, now=utcnow())['done'] == 1
    assert wl._offer_rows(db, salon['business']['id']) == []
    assert _offer_mail(db) == 0


def test_restore_is_refused_while_the_slot_is_offered(client, db, salon):
    assert wl._join(client, salon).status_code == 202
    visit = wl._visit(client, salon)
    assert client.delete(f"/visits/{visit['id']}", headers=salon['headers']).status_code == 200
    offers = wl._offer_rows(db, salon['business']['id'])
    assert [offer.status for offer in offers] == ['pending']
    jobs_before = _jobs(db)

    refused = _restore(client, salon, visit['id'])
    assert refused.status_code == 409, refused.text
    assert refused.json()['error']['detail']['code'] == 'visit_restore_slot_offered'
    assert _deleted(db, visit['id'])
    assert _jobs(db) == jobs_before
    assert [offer.status for offer in wl._offer_rows(db, salon['business']['id'])] == ['pending']

    # once the guest says no, the salon can take the slot back
    token = wl._offer_token(db, salon['business']['id'], 'wait.guest@example.com')
    assert client.post('/public/waitlist/offer/decline', headers=wl._headers(token)).status_code == 200
    restored = _restore(client, salon, visit['id'])
    assert restored.status_code == 200, restored.text
    assert not _deleted(db, visit['id'])
    assert set(_jobs(db)) == {'cancelled'}


def test_an_expired_offer_does_not_block_the_restore(client, db, salon):
    assert wl._join(client, salon).status_code == 202
    visit = wl._visit(client, salon)
    assert client.delete(f"/visits/{visit['id']}", headers=salon['headers']).status_code == 200
    db.query(WaitlistOffer).update({WaitlistOffer.expires_at: utcnow() - timedelta(minutes=1)})
    db.commit()

    restored = _restore(client, salon, visit['id'])
    assert restored.status_code == 200, restored.text
    assert not _deleted(db, visit['id'])


def test_restore_onto_a_slot_won_from_the_waitlist_is_refused(client, db, salon):
    assert wl._join(client, salon).status_code == 202
    visit = wl._visit(client, salon)
    assert client.delete(f"/visits/{visit['id']}", headers=salon['headers']).status_code == 200
    token = wl._offer_token(db, salon['business']['id'], 'wait.guest@example.com')
    assert client.post('/public/waitlist/offer/accept', headers=wl._headers(token)).status_code == 200

    refused = _restore(client, salon, visit['id'])
    assert refused.status_code == 409, refused.text
    assert _deleted(db, visit['id'])
    db.expire_all()
    live = db.query(Visit).filter(Visit.deleted_at.is_(None), Visit.date_start == salon['start']).count()
    assert live == 1


def test_restoring_a_cancelled_visit_keeps_the_offer_alone(client, db, salon):
    # a cancelled visit holds no slot, so bringing it back takes nothing from the guest
    assert wl._join(client, salon).status_code == 202
    visit = wl._visit(client, salon)
    assert wl._cancel(client, salon, visit['id']).status_code == 200
    assert client.delete(f"/visits/{visit['id']}", headers=salon['headers']).status_code == 200
    assert [offer.status for offer in wl._offer_rows(db, salon['business']['id'])] == ['pending']

    restored = _restore(client, salon, visit['id'])
    assert restored.status_code == 200, restored.text
    assert [offer.status for offer in wl._offer_rows(db, salon['business']['id'])] == ['pending']
