"""Saving the same visit chain must not fetch a new catalogue quote."""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.api.crud.crud_visit import prospective_update_lines, update_visit
from app.api.core.visit_settlement_guard import VisitPriceChangeBlocked
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.service import Service
from app.db.models.user import User
from app.db.models.visit import Visit
from app.db.models.visit_item import VisitItem
from app.schemas.visit import VisitUpdate

START = datetime(2026, 11, 20, 12)


@pytest.fixture
def world(db):
    business = Business(name="Snapshot salon", business_type="salon", currency="EUR")
    db.add(business)
    db.flush()
    staff = User(full_name="Owner", email="q1@example.com", hashed_password="synthetic",
                 role="business_owner", business_id=business.id)
    other = User(full_name="Other", email="q1-other@example.com", hashed_password="synthetic",
                 role="specialist", business_id=business.id)
    customer = Client(full_name="Customer")
    db.add_all([staff, other, customer])
    db.flush()
    services = [Service(name=f"New EUR name {i}", business_id=business.id, specialist_id=staff.id,
                        price=40 + i, duration_minutes=90, buffer_before_minutes=20,
                        buffer_after_minutes=25) for i in range(2)]
    db.add_all(services)
    db.flush()
    visit = Visit(client_id=customer.id, specialist_id=staff.id, business_id=business.id,
                  service_id=services[0].id, service_name="Old CZK name 0", price=999.75,
                  currency_snapshot="CZK", status="planned", date_start=START,
                  date_end=START + timedelta(minutes=60))
    db.add(visit)
    db.flush()
    for i, service in enumerate(services):
        visit.items.append(VisitItem(position=i, service_id=service.id, specialist_id=staff.id,
            service_name=f"Old CZK name {i}", price=Decimal("500.00"), duration_minutes=30,
            buffer_before_minutes=2, buffer_after_minutes=3,
            date_start=START + timedelta(minutes=30 * i),
            date_end=START + timedelta(minutes=30 * i + 30), active=True))
    db.commit()
    return visit, services, other


def snapshot(visit):
    return [(row.service_id, row.specialist_id, row.service_name, row.price,
             row.duration_minutes, row.buffer_before_minutes, row.buffer_after_minutes)
            for row in sorted(visit.items, key=lambda row: row.position)]


@pytest.mark.parametrize("total", [999.75, None])
@pytest.mark.parametrize("unit", ["CZK", None])
@pytest.mark.parametrize("kind", ["partial", "full", "same_items", "null_items", "metadata"])
def test_same_chain_preserves_old_snapshots_and_exact_parent_total(db, world, total, unit, kind):
    visit, services, _ = world
    visit.price, visit.currency_snapshot = total, unit
    db.commit()
    before = snapshot(visit)
    items = [{"service_id": service.id} for service in services]
    body = {"date_start": START + timedelta(days=1)}
    if kind == "full":
        body.update(items=items, price=123, date_end=START + timedelta(days=1, minutes=60), note="full")
    elif kind == "same_items":
        body = {"items": items, "price": 123}
    elif kind == "null_items":
        body["items"] = None
    elif kind == "metadata":
        body = {"status": "in_progress", "note": "note"}
    result = update_visit(db, visit, VisitUpdate(**body))
    db.expire_all()
    assert result.price == total
    assert result.currency_snapshot == unit
    assert snapshot(result) == before
    if "date_start" in body:
        assert result.date_start == body["date_start"]
        assert result.date_end == body["date_start"] + timedelta(minutes=60)


@pytest.mark.parametrize("items", [False, True])
def test_prospective_same_chain_uses_stored_names_prices_and_time(db, world, items):
    visit, services, _ = world
    body = {"items": [{"service_id": row.id, "specialist_id": visit.specialist_id} for row in services]} if items else {}
    lines = prospective_update_lines(db, visit, VisitUpdate(**body))
    assert [(line.service_name, line.price, line.duration_minutes,
             line.buffer_before_minutes, line.buffer_after_minutes) for line in lines] == [
                 (f"Old CZK name {i}", Decimal(500), 30, 2, 3) for i in range(2)]


@pytest.mark.parametrize("total", [None, 999.75])
def test_single_explicit_duration_preserves_legacy_total_and_item_price(db, world, total):
    visit, _, _ = world
    visit.items.pop()
    visit.price = total
    db.commit()
    result = update_visit(db, visit, VisitUpdate(date_end=START + timedelta(minutes=80)))
    assert result.price == total
    assert result.items[0].price == Decimal(500)
    assert result.items[0].duration_minutes == 80
    assert result.currency_snapshot == "CZK"


@pytest.mark.parametrize("null_items", [False, True])
def test_single_explicit_price_without_replacement_items_still_updates(db, world, null_items):
    visit, _, _ = world
    visit.items.pop()
    db.commit()
    body = {"price": 321.25}
    if null_items:
        body["items"] = None
    result = update_visit(db, visit, VisitUpdate(**body))
    assert result.price == 321.25
    assert result.items[0].price == Decimal("321.25")
    assert result.currency_snapshot == "CZK"


@pytest.mark.parametrize("change", ["order", "master", "service"])
@pytest.mark.parametrize("unit", ["EUR", "CZK"])
def test_changed_chain_requires_a_matching_catalog_unit(db, world, change, unit):
    visit, services, other = world
    items = [{"service_id": row.id} for row in services]
    if change == "order":
        items.reverse()
    elif change == "master":
        items[0]["specialist_id"] = other.id
    else:
        items = [{"service_id": services[1].id}]
    visit.currency_snapshot = unit
    db.commit()
    before = (visit.price, visit.currency_snapshot, snapshot(visit))
    if unit == "CZK":
        with pytest.raises(VisitPriceChangeBlocked) as blocked:
            update_visit(db, visit, VisitUpdate(items=items, price=123))
        assert blocked.value.reason == "visit_price_currency_explicit_required"
        db.expire_all()
        assert (visit.price, visit.currency_snapshot, snapshot(visit)) == before
        return
    result = update_visit(db, visit, VisitUpdate(items=items, price=123))
    assert result.price == (81 if change != "service" else 41)
    assert result.items[0].service_name.startswith("New EUR name")
    assert result.items[0].duration_minutes == 90
    assert result.currency_snapshot == "EUR"


def test_empty_items_remain_invalid():
    with pytest.raises(ValidationError):
        VisitUpdate(items=[])


@pytest.mark.parametrize("total", [None, 999.75])
@pytest.mark.parametrize("kind", ["move", "duration", "same_items"])
def test_legacy_no_items_preserves_parent_total_and_unknown_unit(db, world, total, kind):
    visit, services, _ = world
    visit.items.clear()
    visit.price, visit.currency_snapshot = total, None
    db.commit()
    body = {"date_start": START + timedelta(days=1)}
    if kind == "duration":
        body = {"date_end": START + timedelta(minutes=80)}
    elif kind == "same_items":
        body = {"items": [{"service_id": services[0].id}], "price": 123}
    result = update_visit(db, visit, VisitUpdate(**body))
    assert result.price == total
    assert result.currency_snapshot is None


@pytest.mark.parametrize("unit", ["EUR", "CZK"])
def test_explicit_service_change_without_items_requires_matching_unit(db, world, unit):
    visit, services, _ = world
    visit.items.pop()
    visit.currency_snapshot = unit
    db.commit()
    before = (visit.price, visit.currency_snapshot, snapshot(visit))
    if unit == "CZK":
        with pytest.raises(VisitPriceChangeBlocked) as blocked:
            update_visit(db, visit, VisitUpdate(service_id=services[1].id))
        assert blocked.value.reason == "visit_price_currency_explicit_required"
        db.expire_all()
        assert (visit.price, visit.currency_snapshot, snapshot(visit)) == before
        return
    result = update_visit(db, visit, VisitUpdate(service_id=services[1].id))
    assert result.service_id == services[1].id
    assert result.price == 41
    assert result.items[0].duration_minutes == 90
