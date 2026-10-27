"""A selected unflushed catalog identity must not turn an unknown unit into a known one."""
import pytest

from app.api.core.config import settings
from app.api.crud import crud_visit
from app.db.models.service import Service
from app.db.models.user import User
from app.db.models.visit import Visit
from tests import test_visit_currency_writers as writer_fixtures

world = writer_fixtures.world


@pytest.mark.parametrize("kind", ["new_explicit_id", "changed_primary_key"])
def test_unflushed_selected_foreign_catalog_identity_stays_unknown_without_losing_caller_uow(db, world, monkeypatch, kind):
    monkeypatch.setattr(settings, "pilot_mode_enabled", False)
    actor = db.get(User, world["owner"])
    actor.role = "admin"
    db.commit()
    target_id = 98765
    if kind == "new_explicit_id":
        service = Service(id=target_id, business_id=world["foreign"], specialist_id=world["other_owner"],
                          name="Unflushed foreign EUR catalog", price=321, duration_minutes=30, is_active=True)
        db.add(service)
    else:
        service = db.get(Service, world["other_service"])
        service.id = target_id
    payload = writer_fixtures.crm_payload(world, service_id=target_id, price=321)
    visit = crud_visit.create_visit(db, payload, business_id=world["business"], actor=actor)
    visit_id = visit.id
    db.expire_all()
    assert db.get(Service, target_id).business_id == world["foreign"]
    assert db.get(Service, target_id).price == 321
    stored = db.get(Visit, visit_id)
    assert stored.price == 321 and stored.business_id == world["business"]
    # The existing core cannot load an unflushed ID; its old service_id=None behavior stays unchanged.
    assert stored.service_id is None
    assert stored.currency_snapshot is None
