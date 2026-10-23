"""Database protection must follow item masters, including direct writers."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.client import Client
from app.db.models.visit import Visit
from app.db.models.visit_item import VisitItem
from tests import test_multi_service as multi

salon = multi.salon


def test_first_master_is_free_while_the_second_master_works(client, salon):
    body = multi._chain_body(salon, items=[
        {'service_id': salon['first']['id']},
        {'service_id': salon['employee_service']['id'], 'specialist_id': salon['employee_user_id']},
    ])
    created = client.post('/visits/', json=body, headers=salon['headers'])
    assert created.status_code == 200, created.text
    body = multi._chain_body(salon, start=salon['start'] + timedelta(minutes=40), items=[
        {'service_id': salon['first']['id']},
    ])
    second = client.post('/visits/', json=body, headers=salon['headers'])
    assert second.status_code == 200, second.text


def direct_world(db, make_user):
    first, second, third = [make_user('specialist') for _ in range(3)]
    person = Client(owner_id=first.id, full_name='Projection client')
    db.add(person)
    db.flush()
    start = datetime(2026, 11, 1, 10)
    return first, second, third, person, start


def parent(person, master, start, status='planned'):
    return Visit(client_id=person.id, specialist_id=master.id, date_start=start,
                 date_end=start + timedelta(hours=1), status=status)


def item(visit, master, start, before=0, after=0, position=0):
    return VisitItem(visit_id=visit.id, position=position, specialist_id=master.id,
                     date_start=start, date_end=start + timedelta(minutes=30), duration_minutes=30,
                     buffer_before_minutes=before, buffer_after_minutes=after)


@pytest.mark.parametrize('buffer_only', [False, True])
def test_direct_parent_writer_conflicts_with_secondary_master(db, make_user, buffer_only):
    first, second, _, person, start = direct_world(db, make_user)
    visit = parent(person, first, start)
    db.add(visit)
    db.flush()
    db.add_all([item(visit, first, start),
                item(visit, second, start + timedelta(minutes=30), after=15, position=1)])
    db.commit()
    candidate_start = start + timedelta(minutes=65 if buffer_only else 40)
    db.add(parent(person, second, candidate_start))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    assert db.query(Visit).count() == 1


def test_two_direct_item_writers_cannot_book_the_same_secondary_master(db, make_user):
    first, second, third, person, start = direct_world(db, make_user)
    db.commit()
    person_id = person.id
    first_ids = [first.id, third.id]
    master_id = second.id
    barrier = Barrier(2)
    engine = db.get_bind()

    def book(first_id):
        with Session(engine) as session:
            session.execute(text("SET LOCAL lock_timeout = '5s'"))
            barrier.wait(timeout=10)
            visit = Visit(client_id=person_id, specialist_id=first_id, date_start=start,
                          date_end=start + timedelta(hours=1), status='planned')
            session.add(visit)
            session.flush()
            session.add_all([
                VisitItem(visit_id=visit.id, position=0, specialist_id=first_id,
                          date_start=start, date_end=start + timedelta(minutes=30), duration_minutes=30),
                VisitItem(visit_id=visit.id, position=1, specialist_id=master_id,
                          date_start=start + timedelta(minutes=30), date_end=start + timedelta(hours=1),
                          duration_minutes=30),
            ])
            try:
                session.commit()
                return 'booked'
            except IntegrityError:
                session.rollback()
                return 'conflict'

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(book, first_ids))
    assert sorted(results) == ['booked', 'conflict']
    assert db.query(Visit).count() == 1
    assert db.query(VisitItem).count() == 2
