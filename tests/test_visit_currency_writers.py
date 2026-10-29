"""New ordinary bookings snapshot a fresh, unambiguous unit in their transaction."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta
from threading import Event
import time as wall_time

import pytest
from sqlalchemy import event, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.core import public_booking_service as public
from app.api.core.clock import utcnow
from app.api.core.config import settings
from app.api.crud import crud_visit
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.client_business_relation import ClientBusinessRelation
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.public_booking import PublicBookingRequest, PublicBookingSettings
from app.db.models.service import Service
from app.db.models.subscription import Subscription
from app.db.models.subscription_plan import SubscriptionPlan
from app.db.models.user import User
from app.db.models.visit import Visit
from app.db.models.visit_item import VisitItem
from app.db.models.work_schedule import WorkSchedule
from app.schemas.public_booking import PublicBookingCreate
from app.schemas.visit import VisitCreate, VisitUpdate


@pytest.fixture
def world(db, monkeypatch):
    monkeypatch.setattr(settings, "pilot_mode_enabled", True)
    monkeypatch.setattr(settings, "service_payment_provider", "disabled")
    now = utcnow()
    business = Business(name="Currency writers", business_type="salon", currency="CZK", timezone="UTC")
    foreign = Business(name="Foreign catalog", business_type="salon", currency="EUR", timezone="UTC")
    db.add_all([business, foreign])
    db.flush()
    owner = User(full_name="Writer Owner", email="writer-owner@example.com", hashed_password="synthetic",
                 role="business_owner", business_id=business.id)
    other = User(full_name="Foreign Owner", email="foreign-owner@example.com", hashed_password="synthetic",
                 role="business_owner", business_id=foreign.id)
    db.add_all([owner, other])
    db.flush()
    client = Client(full_name="Writer Client", owner_id=owner.id, contact_email="writer-client@example.com")
    plan = SubscriptionPlan(code="currency-writer-plan", name="Writer paid plan", period_days=30,
                            price=100, currency="CZK", is_trial=False)
    db.add_all([client, plan])
    db.flush()
    db.add(Subscription(user_id=owner.id, plan_id=plan.id, status="active", is_trial=False,
                        current_period_end=now + timedelta(days=30)))
    service = Service(name="Own catalog", specialist_id=owner.id, business_id=business.id,
                      price=100, duration_minutes=30, is_active=True)
    other_service = Service(name="Foreign catalog", specialist_id=other.id, business_id=foreign.id,
                            price=321, duration_minutes=30, is_active=True)
    db.add_all([service, other_service])
    db.flush()
    db.add(ClientBusinessRelation(client_id=client.id, business_id=business.id))
    db.add(PublicBookingSettings(business_id=business.id, slug="currency-writers", is_enabled=True,
                                deposit_type="none", deposit_value=0))
    db.add_all([WorkSchedule(specialist_id=owner.id, weekday=day, start_time=time(9), end_time=time(18)) for day in range(7)])
    start = (now + timedelta(days=5)).replace(hour=10, minute=0, second=0, microsecond=0)
    result = {"business": business.id, "foreign": foreign.id, "owner": owner.id, "other_owner": other.id,
              "client": client.id, "service": service.id, "other_service": other_service.id, "start": start}
    db.commit()
    db.rollback()
    return result


def crm_payload(world, *, offset=0, **changes):
    data = {"client_id": world["client"], "specialist_id": world["owner"], "service_id": world["service"],
            "date_start": world["start"] + timedelta(hours=offset), "price": None}
    data.update(changes)
    return VisitCreate(**data)


def guest_payload(world, *, offset=0, key="currency-guest-1", **changes):
    data = {"service_id": world["service"], "staff_id": world["owner"],
            "start": world["start"] + timedelta(hours=offset), "full_name": "Guest", "email": f"{key}@example.com",
            "processing_consent": True, "client_request_id": key}
    data.update(changes)
    return PublicBookingCreate(**data)


def create_crm(db, world, **changes):
    return crud_visit.create_visit(db, crm_payload(world, **changes), business_id=world["business"],
                                  actor=db.get(User, world["owner"]))


def create_guest(db, world, *, commit=True, **changes):
    return public.submit_booking(db, db.get(Business, world["business"]), guest_payload(world, **changes), "en", commit=commit)


def test_new_crm_creation_snapshots_business_currency_and_items_inherit_the_parent(db, world):
    visit = create_crm(db, world)
    assert visit.currency_snapshot == "CZK" and visit.price == 100
    assert len(visit.items) == 1 and float(visit.items[0].price) == 100
    assert "currency_snapshot" not in VisitItem.__table__.c


def test_new_guest_creation_snapshots_business_currency(db, world):
    visit = create_guest(db, world)
    assert visit.currency_snapshot == "CZK" and visit.price == 100
    assert db.query(PublicBookingRequest).one().visit_id == visit.id


@pytest.mark.parametrize("kind", ["crm", "guest"])
def test_new_booking_after_currency_change_has_new_unit_while_move_status_and_replay_keep_old(db, world, kind):
    create = create_crm if kind == "crm" else create_guest
    first = create(db, world)
    first_id = first.id
    db.execute(update(Business).where(Business.id == world["business"]).values(currency="EUR"))
    db.execute(update(Service).where(Service.id == world["service"]).values(price=200))
    db.commit()
    second = create(db, world, offset=1, **({"key": "currency-guest-2"} if kind == "guest" else {}))
    assert second.currency_snapshot == "EUR" and second.price == 200
    old = db.get(Visit, first_id)
    if kind == "guest":
        replay = create_guest(db, world)
        assert replay.id == first_id and replay.currency_snapshot == "CZK" and replay.price == 100
        old = replay
    crud_visit.update_visit(db, old, VisitUpdate(date_start=world["start"] + timedelta(hours=2)))
    crud_visit.update_visit(db, old, VisitUpdate(status="cancelled"))
    assert old.currency_snapshot == "CZK" and old.price == 100
    assert float(old.items[0].price) == 100


@pytest.mark.parametrize("currency", ["", "ZZZ", "czk", "XXX"])
@pytest.mark.parametrize("kind", ["crm", "guest"])
def test_invalid_legacy_business_currency_is_unknown_without_eur_fallback(db, world, currency, kind):
    db.execute(update(Business).where(Business.id == world["business"]).values(currency=currency))
    db.commit()
    visit = (create_crm if kind == "crm" else create_guest)(db, world)
    assert visit.currency_snapshot is None and visit.price == 100


def test_solo_creation_does_not_guess_business_unit(db, world):
    db.execute(update(User).where(User.id == world["owner"]).values(business_id=None))
    db.commit()
    db.expire_all()
    visit = crud_visit.create_visit(db, crm_payload(world, service_id=None, price=123), actor=db.get(User, world["owner"]))
    assert visit.business_id is None and visit.currency_snapshot is None and visit.price == 123


@pytest.mark.parametrize("mode", ["fallback_none", "fallback_zero", "copied_nonzero", "items", "mixed_items"])
def test_admin_foreign_catalog_price_is_never_labeled_with_target_business_currency(client, db, world, headers_for, mode):
    db.execute(update(User).where(User.id == world["owner"]).values(role="admin"))
    db.commit()
    actor = db.get(User, world["owner"])
    body = crm_payload(world).model_dump(mode="json")
    body["service_id"] = world["other_service"]
    body["price"] = {"fallback_none": None, "fallback_zero": 0, "copied_nonzero": 321}.get(mode, 999)
    if mode in {"items", "mixed_items"}:
        body["items"] = ([{"service_id": world["service"]}] if mode == "mixed_items" else []) + [{"service_id": world["other_service"]}]
    response = client.post("/visits/", json=body, headers=headers_for(actor))
    assert response.status_code == 200, response.text
    visit = db.get(Visit, response.json()["id"])
    assert visit.business_id == world["business"] and visit.currency_snapshot is None
    assert visit.price == (421 if mode == "mixed_items" else 321)


def test_manual_price_without_service_is_in_the_explicit_target_business_unit(db, world):
    visit = create_crm(db, world, service_id=None, price=777)
    assert visit.price == 777 and visit.currency_snapshot == "CZK"


@pytest.mark.parametrize("kind", ["crm", "guest"])
def test_legacy_unscoped_catalog_price_stays_unknown_without_changing_existing_permissions(db, world, kind):
    db.execute(update(Service).where(Service.id == world["service"]).values(business_id=None))
    db.commit()
    visit = (create_crm if kind == "crm" else create_guest)(db, world)
    assert visit.price == 100 and visit.currency_snapshot is None


def test_http_body_cannot_choose_snapshot_currency(client, db, world, headers_for):
    body = crm_payload(world).model_dump(mode="json")
    body["currency_snapshot"] = "USD"
    response = client.post("/visits/", json=body, headers=headers_for(db.get(User, world["owner"])))
    assert response.status_code == 200, response.text
    assert db.get(Visit, response.json()["id"]).currency_snapshot == "CZK"
    payload = guest_payload(world, offset=1, key="body-owned-guest").model_dump(mode="json")
    payload["currency_snapshot"] = "USD"
    refused = client.post("/public/booking/currency-writers/bookings", json=payload)
    assert refused.status_code == 422 and db.query(Visit).count() == 1


def wait_for_blocker(engine, waiter_pid, holder_pid):
    end = wall_time.monotonic() + 8
    with engine.connect() as observer:
        while wall_time.monotonic() < end:
            if holder_pid in observer.execute(text("SELECT pg_blocking_pids(:pid)"), {"pid": waiter_pid}).scalar_one():
                return
            wall_time.sleep(0.01)
    pytest.fail("PostgreSQL did not confirm the expected lock wait")


def run_booking(engine, world, kind):
    with Session(engine) as transaction:
        # Deliberately cache both objects before the writer reaches its lock wait.
        business = transaction.get(Business, world["business"])
        service = transaction.get(Service, world["service"])
        assert business.currency == "CZK" and service.price == 100
        visit = (create_crm if kind == "crm" else create_guest)(transaction, world)
        return visit.id, visit.currency_snapshot, visit.price, float(visit.items[0].price)


@pytest.mark.parametrize("kind", ["crm", "guest"])
def test_currency_guard_waits_for_update_then_reads_new_scalar_and_catalog_instead_of_cache(db, world, kind):
    engine = db.get_bind()
    waiting = Event()
    observed = {}
    def query(conn, _cursor, statement, *_args):
        if "FROM businesses" in statement and "FOR SHARE" in statement:
            observed["pid"] = conn.connection.driver_connection.get_backend_pid()
            waiting.set()
    with Session(engine) as holder:
        holder.execute(update(Business).where(Business.id == world["business"]).values(currency="EUR"))
        holder.execute(update(Service).where(Service.id == world["service"]).values(price=200, duration_minutes=45))
        holder_pid = holder.execute(text("SELECT pg_backend_pid()")).scalar_one()
        event.listen(engine, "before_cursor_execute", query)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(run_booking, engine, world, kind)
                try:
                    assert waiting.wait(timeout=8)
                    wait_for_blocker(engine, observed["pid"], holder_pid)
                    assert not future.done()
                finally:
                    holder.commit()
                result = future.result(timeout=15)
        finally:
            event.remove(engine, "before_cursor_execute", query)
    assert result[1:] == ("EUR", 200, 200)
    assert db.get(Visit, result[0]).date_end - world["start"] == timedelta(minutes=45)


@pytest.mark.parametrize("kind,change", [("crm", "currency_and_price"), ("guest", "currency_and_price"), ("guest", "service_disabled"), ("guest", "slot_policy")])
def test_writers_take_user_locks_before_business_then_refresh_cached_catalog(db, world, kind, change):
    engine = db.get_bind()
    waiting = Event()
    observed = {}
    def query(conn, _cursor, statement, *_args):
        lock = "FOR UPDATE" if kind == "guest" else "FOR KEY SHARE"
        if "FROM users" in statement and lock in statement:
            observed["pid"] = conn.connection.driver_connection.get_backend_pid()
            waiting.set()
    def book():
        try:
            return run_booking(engine, world, kind)
        except public.ServiceUnavailable:
            return "unavailable"
        except public.SlotUnavailable:
            return "slot_unavailable"
    with Session(engine) as holder:
        holder.query(User).filter(User.id == world["owner"]).with_for_update().one()
        holder_pid = holder.execute(text("SELECT pg_backend_pid()")).scalar_one()
        event.listen(engine, "before_cursor_execute", query)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(book)
                try:
                    assert waiting.wait(timeout=8)
                    wait_for_blocker(engine, observed["pid"], holder_pid)
                    holder.execute(update(Business).where(Business.id == world["business"]).values(
                        currency="EUR", booking_lead_time_minutes=10080 if change == "slot_policy" else 0))
                    holder.execute(update(Service).where(Service.id == world["service"]).values(
                        price=200, is_active=change != "service_disabled"))
                finally:
                    holder.commit()
                result = future.result(timeout=15)
        finally:
            event.remove(engine, "before_cursor_execute", query)
    if change in {"service_disabled", "slot_policy"}:
        assert result == ("unavailable" if change == "service_disabled" else "slot_unavailable")
        assert db.query(Visit).count() == db.query(PublicBookingRequest).count() == 0
    else:
        assert result[1:] == ("EUR", 200, 200)


@pytest.mark.parametrize("kind", ["crm", "guest"])
def test_new_booking_holds_business_share_through_commit_so_currency_update_waits(db, world, kind):
    engine = db.get_bind()
    committing, release, updating = Event(), Event(), Event()
    observed = {}
    def pause(transaction):
        if transaction.info.get("pause_currency_booking"):
            observed["holder"] = transaction.execute(text("SELECT pg_backend_pid()")).scalar_one()
            committing.set()
            assert release.wait(timeout=12)
    def book():
        with Session(engine) as transaction:
            transaction.info["pause_currency_booking"] = True
            visit = (create_crm if kind == "crm" else create_guest)(transaction, world)
            return visit.id
    def update_currency():
        with Session(engine) as transaction:
            observed["waiter"] = transaction.execute(text("SELECT pg_backend_pid()")).scalar_one()
            updating.set()
            transaction.execute(update(Business).where(Business.id == world["business"]).values(currency="EUR"))
            transaction.commit()
    event.listen(Session, "before_commit", pause)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            booking = pool.submit(book)
            try:
                assert committing.wait(timeout=8)
                changed = pool.submit(update_currency)
                assert updating.wait(timeout=8)
                wait_for_blocker(engine, observed["waiter"], observed["holder"])
                assert not changed.done()
            finally:
                release.set()
            visit_id = booking.result(timeout=15)
            changed.result(timeout=15)
    finally:
        event.remove(Session, "before_commit", pause)
    db.expire_all()
    assert db.get(Visit, visit_id).currency_snapshot == "CZK"
    assert db.get(Business, world["business"]).currency == "EUR"


@pytest.mark.parametrize("finalize", ["commit", "rollback"])
def test_waitlist_style_commit_false_keeps_currency_lock_until_caller_finalizes(db, world, finalize):
    engine = db.get_bind()
    started = Event()
    observed = {}
    def update_currency():
        with Session(engine) as transaction:
            observed["waiter"] = transaction.execute(text("SELECT pg_backend_pid()")).scalar_one()
            started.set()
            transaction.execute(update(Business).where(Business.id == world["business"]).values(currency="EUR"))
            transaction.commit()
    with Session(engine) as caller:
        visit = create_guest(caller, world, commit=False)
        visit_id = visit.id
        assert caller.in_transaction() and visit.currency_snapshot == "CZK"
        holder_pid = caller.execute(text("SELECT pg_backend_pid()")).scalar_one()
        with Session(engine) as observer:
            assert observer.query(Visit).count() == observer.query(PublicBookingRequest).count() == 0
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(update_currency)
            try:
                assert started.wait(timeout=8)
                wait_for_blocker(engine, observed["waiter"], holder_pid)
                assert not pending.done()
            finally:
                getattr(caller, finalize)()
            pending.result(timeout=15)
    db.expire_all()
    assert db.get(Business, world["business"]).currency == "EUR"
    if finalize == "rollback":
        assert db.query(Visit).count() == db.query(VisitItem).count() == db.query(PublicBookingRequest).count() == 0
        assert db.query(NotificationOutbox).count() == 0
    else:
        assert db.get(Visit, visit_id).currency_snapshot == "CZK"


@pytest.mark.parametrize("kind", ["crm", "guest"])
def test_real_deferred_commit_failure_rolls_back_visit_items_claim_and_snapshot(db, world, kind):
    db.execute(text("""CREATE FUNCTION currency_writer_commit_failure() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'synthetic currency writer commit failure'; RETURN NEW; END $$"""))
    db.execute(text("""CREATE CONSTRAINT TRIGGER currency_writer_commit_failure AFTER INSERT ON visits
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION currency_writer_commit_failure()"""))
    db.commit()
    try:
        with pytest.raises(DBAPIError):
            (create_crm if kind == "crm" else create_guest)(db, world)
        db.rollback()
        assert db.query(Visit).count() == db.query(VisitItem).count() == db.query(PublicBookingRequest).count() == 0
        assert db.query(NotificationOutbox).count() == 0
    finally:
        db.rollback()
        db.execute(text("DROP TRIGGER currency_writer_commit_failure ON visits"))
        db.execute(text("DROP FUNCTION currency_writer_commit_failure()"))
        db.commit()
