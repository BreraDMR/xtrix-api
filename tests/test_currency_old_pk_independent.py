"""A pending catalog PK change must keep the older successful caller UoW behavior."""
import pytest

from app.api.core.config import settings
from app.api.crud import crud_visit
from app.db.models.service import Service
from app.db.models.user import User
from app.db.models.visit import Visit
from tests import test_visit_currency_writers as writer_fixtures

world = writer_fixtures.world


@pytest.mark.parametrize('source', ['own', 'foreign'])
def test_pending_service_pk_with_old_requested_id_keeps_successful_baseline_flow(db, world, monkeypatch, source):
    monkeypatch.setattr(settings, 'pilot_mode_enabled', False)
    actor = db.get(User, world['owner'])
    actor.role = 'admin'
    db.commit()
    old_id = world['service' if source == 'own' else 'other_service']
    service = db.get(Service, old_id)
    replacement_id = 98766
    service.id = replacement_id
    payload = writer_fixtures.crm_payload(world, service_id=old_id, price=None)
    visit = crud_visit.create_visit(db, payload, business_id=world['business'], actor=actor)
    visit_id = visit.id
    db.expire_all()
    assert db.get(Service, replacement_id) is not None and db.get(Service, old_id) is None
    stored = db.get(Visit, visit_id)
    assert stored.service_id == replacement_id
    assert stored.price == (100 if source == 'own' else 321)
    assert stored.currency_snapshot == ('CZK' if source == 'own' else None)
