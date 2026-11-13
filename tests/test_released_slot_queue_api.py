"""Every public mutation keeps its original chain in the durable matching queue."""
from datetime import timedelta

import pytest

from app.api.core import waitlist_service
from app.api.core.booking_manage_access import issue_token
from app.db.models.released_slot_job import ReleasedSlotJob as Job
from app.db.models.visit import Visit
from tests import test_multi_service as base

salon = base.salon


@pytest.mark.parametrize('actor', ['crm', 'guest'])
@pytest.mark.parametrize('operation', ['cancel', 'move'])
def test_chain_release_is_durable_and_repeated_requests_do_not_duplicate_jobs(client, db, salon, monkeypatch, actor, operation):
    created = client.post('/visits/', json=base._chain_body(salon), headers=salon['headers'])
    assert created.status_code == 200, created.text
    visit = db.get(Visit, created.json()['id'])
    old = [(item.service_id, item.specialist_id, item.date_start) for item in visit.items]
    raw, _ = issue_token(db, visit)
    calls = []
    def unavailable(_db, _business, *slot):
        calls.append(slot)
        raise RuntimeError('Synthetic postcommit matcher outage')
    monkeypatch.setattr(waitlist_service, 'release_slot', unavailable)
    target = salon['start'] + timedelta(hours=3)
    def command():
        if actor == 'crm':
            body = {'status': 'cancelled'} if operation == 'cancel' else {'date_start': target.isoformat() + 'Z'}
            return client.put(f'/visits/{visit.id}', json=body, headers=salon['headers'])
        body = {} if operation == 'cancel' else {'start': target.isoformat() + 'Z'}
        path = 'cancel' if operation == 'cancel' else 'reschedule'
        return client.post('/public/booking-management/' + path, json=body, headers={'X-Booking-Token': raw})
    response = command()
    assert response.status_code == 200, response.text
    db.expire_all()
    assert [(job.service_id, job.specialist_id, job.start) for job in db.query(Job).order_by(Job.segment)] == old
    assert calls == old
    assert command().status_code == 200
    assert db.query(Job).count() == 2
    assert calls == old
