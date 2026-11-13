"""Deleting a visit keeps every freed segment for the queue, and the worker runs the queue."""
import importlib.util
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.released_slot_job import ReleasedSlotJob as Job
from app.db.models.visit import Visit
from tests import test_completed_payment_guard as base

salon = base.salon


def _jobs(db):
    with Session(db.get_bind()) as fresh:
        return [(job.service_id, job.specialist_id, job.start, job.status)
                for job in fresh.execute(select(Job).order_by(Job.segment)).scalars()]


def _segments(db, visit_id):
    db.expire_all()
    visit = db.get(Visit, visit_id)
    rows = [(item.service_id, item.specialist_id, item.date_start)
            for item in sorted(visit.items, key=lambda item: item.position)]
    db.rollback()
    return rows


def test_soft_delete_saves_every_segment_in_the_same_commit(client, db, salon):
    visit_id, _items = base.create_quote(client, salon)
    segments = _segments(db, visit_id)
    assert len(segments) == 2
    r = client.delete(f'/visits/{visit_id}', headers=salon['headers'])
    assert r.status_code == 200, r.text
    assert [job[:3] for job in _jobs(db)] == segments


def test_deleting_a_cancelled_visit_frees_nothing_new(client, db, salon):
    visit_id, items = base.create_quote(client, salon)
    assert client.put(f'/visits/{visit_id}', headers=salon['headers'], json={'status': 'cancelled'}).status_code == 200
    before = _jobs(db)
    assert client.delete(f'/visits/{visit_id}', headers=salon['headers']).status_code == 200
    assert _jobs(db) == before


def _load_jobs():
    spec = importlib.util.spec_from_file_location('run_jobs', Path(__file__).resolve().parents[1] / 'scripts' / 'run_jobs.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_worker_runs_the_released_slot_queue_every_round(client, db, salon):
    jobs = _load_jobs()
    assert 'released_slots' in jobs.FREQUENT
    visit_id, _items = base.create_quote(client, salon)
    assert client.delete(f'/visits/{visit_id}', headers=salon['headers']).status_code == 200
    assert {job[3] for job in _jobs(db)} == {'pending'}
    assert jobs.run(['released_slots']) is True
    assert {job[3] for job in _jobs(db)} == {'done'}


def test_scheduled_workflow_runs_it_too():
    workflow = (Path(__file__).resolve().parents[1] / '.github' / 'workflows' / 'jobs.yml').read_text()
    assert 'released_slots reminders followups' in workflow
