"""Exact visitor authority and locking on the real local PostgreSQL instance."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta
from threading import Event
from time import monotonic, sleep

import pytest
from sqlalchemy import event, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.core import visitor_action_target as target
from app.api.core import visitor_sessions
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.user import User
from app.db.models.visit import Visit
from app.db.models.visitor_access import VisitorGrant
from app.db.models.visitor_account import VisitorAccount
from app.db.models.visitor_auth import VisitorSession

NOW = datetime(2026, 10, 27, 12)


@pytest.fixture
def world(db, monkeypatch):
    clock = [NOW]
    monkeypatch.setattr(visitor_sessions, "utcnow", lambda: clock[0])
    monkeypatch.setattr(target, "utcnow", lambda: clock[0])
    business = Business(name="Target salon", business_type="salon")
    customer = Client(full_name="Target customer")
    other = Client(full_name="Other customer")
    actor = VisitorAccount(email_canonical="target@example.com", email_verified_at=NOW)
    outsider = VisitorAccount(email_canonical="outside@example.com", email_verified_at=NOW)
    db.add_all([business, customer, other, actor, outsider])
    db.flush()
    staff = User(full_name="Target staff", email="staff-target@example.com", hashed_password="synthetic",
                 role="business_owner", business_id=business.id)
    db.add(staff)
    db.flush()
    visit = Visit(business_id=business.id, client_id=customer.id, specialist_id=staff.id,
                  status="planned", date_start=NOW + timedelta(days=3),
                  date_end=NOW + timedelta(days=3, hours=1), price=999, currency_snapshot=None)
    db.add(visit)
    db.flush()
    grant = VisitorGrant(account_id=actor.id, business_id=business.id, client_id=customer.id,
                         scope="visit", visit_id=visit.id)
    db.add(grant)
    db.commit()
    session, raw = visitor_sessions.create_session(db, visitor_sessions.lock_verified_account(db, actor.id))
    _other_session, other_raw = visitor_sessions.create_session(db, visitor_sessions.lock_verified_account(db, outsider.id))
    _same_session, same_raw = visitor_sessions.create_session(db, visitor_sessions.lock_verified_account(db, actor.id))
    db.commit()
    result = {"business": business.id, "client": customer.id, "other_client": other.id, "staff": staff.id,
              "account": actor.id, "outsider": outsider.id, "visit": visit.id, "grant": grant.id,
              "session": session.id, "raw": raw, "other_raw": other_raw, "same_raw": same_raw, "clock": clock}
    db.rollback()
    return result


def resolve(db, world):
    return target.resolve_action_target(db, world["raw"], world["visit"])


def wait_blocked(engine, pid):
    until = monotonic() + 5
    while monotonic() < until:
        with engine.connect() as observer:
            blockers = observer.execute(text("SELECT pg_blocking_pids(:pid)"), {"pid": pid}).scalar_one()
        if blockers:
            return blockers
        sleep(0.01)
    raise AssertionError("Worker never reached the real PostgreSQL wait")


@pytest.mark.parametrize("scope", ["visit", "client", "both"])
def test_exact_grants_and_frozen_facts(db, world, scope):
    if scope in {"client", "both"}:
        if scope == "client":
            db.delete(db.get(VisitorGrant, world["grant"]))
        db.add(VisitorGrant(account_id=world["account"], business_id=world["business"],
                           client_id=world["client"], scope="client", visit_id=None))
        db.commit()
    facts = resolve(db, world)
    assert facts.visit_id == world["visit"] and facts.account_id == world["account"]
    assert facts.session_id == world["session"] and facts.client_id == world["client"]
    assert facts.status == "planned" and facts.date_start == NOW + timedelta(days=3)
    assert facts.client_confirmed_at is None and facts.checked_at == NOW
    if scope == "both":
        assert facts.grant_id == world["grant"]
    with pytest.raises(FrozenInstanceError):
        facts.status = "cancelled"
    assert target.revalidate_action_target(db, world["raw"], facts) == facts
    db.rollback()


@pytest.mark.parametrize("kind", ["unknown", "foreign", "revoked", "visit_deleted", "client_deleted",
                                 "business_deleted", "retargeted", "wrong_grant_client", "wrong_grant_business"])
def test_unavailable_is_one_typed_error_without_fallback(db, world, kind):
    raw, visit_id = world["raw"], world["visit"]
    if kind == "unknown":
        visit_id += 999
    elif kind == "foreign":
        raw = world["other_raw"]
    elif kind == "revoked":
        db.get(VisitorGrant, world["grant"]).revoked_at = NOW
    elif kind.endswith("_deleted"):
        model, key = {"visit_deleted": (Visit, "visit"), "client_deleted": (Client, "client"),
                      "business_deleted": (Business, "business")}[kind]
        db.get(model, world[key]).deleted_at = NOW
    elif kind == "retargeted":
        db.get(Visit, world["visit"]).client_id = world["other_client"]
    elif kind == "wrong_grant_client":
        db.get(VisitorGrant, world["grant"]).client_id = world["other_client"]
    else:
        other = Business(name="Other salon", business_type="salon")
        db.add(other)
        db.flush()
        db.get(VisitorGrant, world["grant"]).business_id = other.id
    db.commit()
    with pytest.raises(target.ActionTargetUnavailable) as error:
        target.resolve_action_target(db, raw, visit_id)
    assert str(error.value) == "Visitor action target is not available"
    db.rollback()


@pytest.mark.parametrize("raw", [None, "", "not-a-token", "vst_" + "x" * 43])
def test_bad_session_has_own_error(db, world, raw):
    with pytest.raises(target.InvalidActionSession):
        target.resolve_action_target(db, raw, world["visit"])
    db.rollback()


def test_stale_identity_map_cannot_hide_committed_revocation(db, world):
    cached = db.get(VisitorGrant, world["grant"])
    with Session(db.get_bind()) as writer:
        writer.execute(update(VisitorGrant).where(VisitorGrant.id == cached.id).values(revoked_at=NOW))
        writer.commit()
    assert cached.revoked_at is None
    with pytest.raises(target.ActionTargetUnavailable):
        resolve(db, world)
    db.rollback()


@pytest.mark.parametrize("model,key,field,value", [
    (Visit, "visit", "status", "cancelled"), (Business, "business", "name", "dirty"),
    (Client, "client", "full_name", "dirty"), (VisitorAccount, "account", "email_canonical", "dirty@example.com"),
    (VisitorSession, "session", "revoked_at", NOW), (VisitorGrant, "grant", "revoked_at", NOW)])
def test_dirty_targets_are_not_erased_or_flushed(db, world, model, key, field, value):
    row = db.get(model, world[key])
    setattr(row, field, value)
    with pytest.raises(target.DirtyActionState):
        resolve(db, world)
    assert getattr(row, field) == value
    db.rollback()


def test_unrelated_pending_rows_no_autoflush_no_writes_no_commit(db, world):
    pending = Client(full_name="Must stay pending")
    db.add(pending)
    statements = []
    def observe(conn, cursor, statement, params, context, executemany):
        statements.append(statement.lstrip().upper())
    event.listen(db.get_bind(), "before_cursor_execute", observe)
    db.autoflush = True
    try:
        facts = resolve(db, world)
        target.revalidate_action_target(db, world["raw"], facts)
        assert pending.id is None and pending in db.new
        assert not any(sql.startswith(("INSERT", "UPDATE", "DELETE", "SAVEPOINT", "COMMIT")) for sql in statements)
        with Session(db.get_bind()) as observer:
            assert observer.query(Client).filter(Client.full_name == pending.full_name).count() == 0
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", observe)
        db.rollback()


@pytest.mark.parametrize("kind", ["expiry", "revoke", "retarget", "delete_client"])
def test_real_business_wait_rechecks_expiry_and_scope(db, world, kind):
    engine = db.get_bind()
    blocker = Session(engine)
    blocker.execute(select(Business.id).where(Business.id == world["business"]).with_for_update())
    ready, pid_box = Event(), []
    def worker():
        with Session(engine) as session:
            session.execute(text("SET LOCAL lock_timeout = '4s'"))
            pid_box.append(session.execute(text("SELECT pg_backend_pid()")).scalar_one())
            ready.set()
            try:
                resolve(session, world)
                return "unexpected-success"
            except (target.InvalidActionSession, target.ActionTargetUnavailable) as error:
                session.rollback()
                return type(error)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(worker)
            assert ready.wait(3)
            assert wait_blocked(engine, pid_box[0])
            if kind == "expiry":
                world["clock"][0] = NOW + timedelta(days=30)
            elif kind == "revoke":
                blocker.execute(update(VisitorGrant).where(VisitorGrant.id == world["grant"]).values(revoked_at=NOW))
            elif kind == "retarget":
                blocker.execute(update(Visit).where(Visit.id == world["visit"]).values(client_id=world["other_client"]))
            else:
                blocker.execute(update(Client).where(Client.id == world["client"]).values(deleted_at=NOW))
            blocker.commit()
            assert future.result(timeout=5) is (target.InvalidActionSession if kind == "expiry" else target.ActionTargetUnavailable)
    finally:
        blocker.rollback()
        blocker.close()


@pytest.mark.parametrize("model,key", [(Visit, "visit"), (Client, "client"), (VisitorGrant, "grant")])
def test_nowait_busy_requires_caller_rollback_and_releases_prior_locks(db, world, model, key):
    engine = db.get_bind()
    with Session(engine) as blocker:
        blocker.execute(select(model.id).where(model.id == world[key]).with_for_update())
        with Session(engine) as worker:
            with pytest.raises(target.ActionBusy):
                resolve(worker, world)
            # NOWAIT leaves a failed transaction. The core must not repair it internally.
            with pytest.raises(DBAPIError):
                worker.execute(text("SELECT 1"))
            worker.rollback()
            with Session(engine) as observer:
                observer.execute(select(VisitorAccount.id).where(VisitorAccount.id == world["account"]).with_for_update(nowait=True))
                if model is not Visit:
                    observer.execute(select(Visit.id).where(Visit.id == world["visit"]).with_for_update(nowait=True))
                if model is VisitorGrant:
                    observer.execute(select(Client.id).where(Client.id == world["client"]).with_for_update(nowait=True))
                observer.rollback()
        blocker.rollback()


@pytest.mark.parametrize("other_key", ["other_raw", "same_raw"])
def test_final_revalidation_uses_current_exact_session(db, world, other_key):
    facts = resolve(db, world)
    with pytest.raises(target.InvalidActionSession):
        target.revalidate_action_target(db, world[other_key], facts)
    world["clock"][0] = NOW + timedelta(days=30)
    with pytest.raises(target.InvalidActionSession):
        target.revalidate_action_target(db, world["raw"], facts)
    db.rollback()


def test_success_keeps_all_locks_until_caller_rollback(db, world):
    resolve(db, world)
    engine = db.get_bind()
    for model, key in [(VisitorAccount, "account"), (Business, "business"), (Visit, "visit"),
                       (Client, "client"), (VisitorGrant, "grant")]:
        with Session(engine) as observer:
            with pytest.raises(DBAPIError) as error:
                observer.execute(select(model.id).where(model.id == world[key]).with_for_update(nowait=True))
            assert error.value.orig.pgcode == "55P03"
            observer.rollback()
    db.rollback()
    with Session(engine) as observer:
        for model, key in [(VisitorAccount, "account"), (Business, "business"), (Visit, "visit"),
                           (Client, "client"), (VisitorGrant, "grant")]:
            observer.execute(select(model.id).where(model.id == world[key]).with_for_update(nowait=True))
        observer.rollback()


@pytest.mark.parametrize("finish", ["commit", "rollback"])
def test_facts_cannot_be_reused_after_their_transaction(db, world, finish):
    facts = resolve(db, world)
    getattr(db, finish)()
    with pytest.raises(target.DirtyActionState):
        target.revalidate_action_target(db, world["raw"], facts)


@pytest.mark.parametrize("status", [None, "", "completed"])
def test_resolver_preserves_raw_status_without_booking_policy(db, world, status):
    db.execute(update(Visit).where(Visit.id == world["visit"]).values(status=status))
    db.commit()
    assert resolve(db, world).status == status
    db.rollback()


def test_revalidation_reads_callers_flushed_changes_without_own_writes(db, world):
    facts = resolve(db, world)
    visit = db.get(Visit, world["visit"])
    visit.status = "cancelled"
    db.flush()
    fresh = target.revalidate_action_target(db, world["raw"], facts)
    assert fresh.status == "cancelled" and fresh.grant_id == facts.grant_id
    db.rollback()
    assert db.get(Visit, world["visit"]).status == "planned"
