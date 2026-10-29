"""The unit and catalog price must describe the same caller transaction."""
from app.api.core.config import settings
from app.api.crud import crud_visit
from app.db.models.business import Business
from app.db.models.service import Service
from app.db.models.user import User
from app.db.models.visit import Visit
from tests import test_visit_currency_writers as writer_fixtures

world=writer_fixtures.world
crm_payload=writer_fixtures.crm_payload


def test_pending_business_and_catalog_edits_do_not_mislabel_new_crm_price(db,world,monkeypatch):
    monkeypatch.setattr(settings,'pilot_mode_enabled',False)
    business=db.get(Business,world['business'])
    service=db.get(Service,world['service'])
    actor=db.get(User,world['owner'])
    payload=crm_payload(world)
    # A caller may have prepared both edits before creating a visit in this Session.
    # No query is run between these changes and the real create call.
    business.currency='EUR'
    service.price=200
    visit=crud_visit.create_visit(db,payload,business_id=world['business'],actor=actor)
    db.expire_all()
    assert db.get(Business,world['business']).currency=='EUR'
    assert db.get(Service,world['service']).price==200
    stored=db.get(Visit,visit.id)
    assert stored.price==200
    assert stored.currency_snapshot=='EUR'


def test_pending_catalog_edit_is_not_silently_lost_when_creating_visit(db,world,monkeypatch):
    monkeypatch.setattr(settings,'pilot_mode_enabled',False)
    service=db.get(Service,world['service'])
    actor=db.get(User,world['owner'])
    payload=crm_payload(world)
    service.price=200
    visit=crud_visit.create_visit(db,payload,business_id=world['business'],actor=actor)
    db.expire_all()
    assert db.get(Service,world['service']).price==200
    assert db.get(Visit,visit.id).price==200


def test_pending_business_edit_is_not_snapshot_as_old_committed_currency(db,world,monkeypatch):
    monkeypatch.setattr(settings,'pilot_mode_enabled',False)
    business=db.get(Business,world['business'])
    actor=db.get(User,world['owner'])
    payload=crm_payload(world)
    business.currency='EUR'
    visit=crud_visit.create_visit(db,payload,business_id=world['business'],actor=actor)
    db.expire_all()
    assert db.get(Business,world['business']).currency=='EUR'
    assert db.get(Visit,visit.id).currency_snapshot=='EUR'


import pytest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from sqlalchemy import event, text, update
from sqlalchemy.orm import Session


@pytest.mark.parametrize('change',['price_duration','catalog_scope','business_name'])
def test_dirty_service_caller_keeps_business_before_service_order_and_refreshes_unchanged_facts(db,world,monkeypatch,change):
    monkeypatch.setattr(settings,'pilot_mode_enabled',False)
    engine=db.get_bind()
    waiting,pids=Event(),{}
    def observe(conn,_cursor,statement,*_args):
        if 'FROM businesses' in statement and ('FOR SHARE' in statement or 'FOR UPDATE' in statement):
            pids['worker']=conn.connection.driver_connection.get_backend_pid();waiting.set()
    def book():
        with Session(engine,autoflush=False) as worker:
            business=worker.get(Business,world['business'])
            service=worker.get(Service,world['service'])
            actor=worker.get(User,world['owner'])
            assert business.currency=='CZK' and service.price==100
            if change=='business_name':business.name='Caller pending name'
            service.price=200
            visit=crud_visit.create_visit(worker,crm_payload(world),business_id=world['business'],actor=actor)
            return visit.id,visit.currency_snapshot,visit.price,visit.date_end-visit.date_start
    with Session(engine) as writer:
        writer.execute(text('SET LOCAL lock_timeout=2000'))
        writer.execute(update(Business).where(Business.id==world['business']).values(currency='EUR'))
        holder_pid=writer.execute(text('SELECT pg_backend_pid()')).scalar_one()
        event.listen(engine,'before_cursor_execute',observe)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(book)
                try:
                    assert waiting.wait(8)
                    writer_fixtures.wait_for_blocker(engine,pids['worker'],holder_pid)
                    # This UPDATE must complete while the creator is still waiting on
                    # Business. An early Service flush would introduce a lock cycle.
                    writer.execute(update(Service).where(Service.id==world['service']).values(
                        price=300,duration_minutes=45,
                        business_id=world['foreign'] if change=='catalog_scope' else world['business']))
                    writer.commit()
                finally:writer.rollback()
                result=future.result(timeout=15)
        finally:event.remove(engine,'before_cursor_execute',observe)
    assert result[1:]==(None if change=='catalog_scope' else 'EUR',200,__import__('datetime').timedelta(minutes=45))
    db.expire_all()
    assert db.get(Service,world['service']).price==200
    assert db.get(Business,world['business']).currency=='EUR'
    if change=='business_name':assert db.get(Business,world['business']).name=='Caller pending name'


def test_guest_pending_currency_and_catalog_edits_survive_creation(db, world, monkeypatch):
    monkeypatch.setattr(settings, 'pilot_mode_enabled', False)
    from app.api.core import public_booking_service
    business = db.get(Business, world['business'])
    service = db.get(Service, world['service'])
    business.currency = 'EUR'
    service.price = 200
    visit = public_booking_service.submit_booking(
        db, business, writer_fixtures.guest_payload(world), 'en')
    visit_id = visit.id
    db.expire_all()
    assert db.get(Business, world['business']).currency == 'EUR'
    assert db.get(Service, world['service']).price == 200
    assert db.get(Visit, visit_id).currency_snapshot == 'EUR'
    assert db.get(Visit, visit_id).price == 200
