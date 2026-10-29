"""Visitor history authorizes exact grants without falling back to CRM identity."""
from dataclasses import FrozenInstanceError, asdict, fields
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, event, select, update
from sqlalchemy.orm import Session

from app.api.core import booking_manage_access, public_booking_service, subscription_access
from app.api.core import visitor_history as history
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.client_business_relation import ClientBusinessRelation
from app.db.models.public_booking import PublicBookingSettings
from app.db.models.user import User
from app.db.models.visit import Visit
from app.db.models.visitor_access import VisitorGrant
from app.db.models.visitor_account import VisitorAccount

NOW = datetime(2026, 10, 26, 12)


def add_visit(db, world, *, business="a", client="client", start=NOW, **values):
    visit = Visit(client_id=world[client], specialist_id=world["staff_a"],
                  business_id=world[business] if business else None,
                  date_start=start, date_end=start + timedelta(minutes=45), status="planned",
                  note="private-note-canary", photo_url="private-photo-canary", price=1234.56)
    for key, value in values.items():
        setattr(visit, key, value)
    db.add(visit)
    db.flush()
    return visit.id


def grant(db, world, *, actor="one", business="a", client="client", visit=None, revoked=False):
    row = VisitorGrant(account_id=world[actor], business_id=world[business], client_id=world[client],
                       scope="client" if visit is None else "visit", visit_id=visit,
                       revoked_at=NOW if revoked else None)
    db.add(row)
    db.flush()
    return row.id


@pytest.fixture
def world(db):
    one = VisitorAccount(email_canonical="shared@example.com", email_verified_at=NOW)
    two = VisitorAccount(email_canonical="other@example.com", email_verified_at=NOW)
    a = Business(name="Salon A", business_type="salon", currency="CZK")
    b = Business(name="Salon B", business_type="salon", currency="EUR")
    db.add_all([one, two, a, b])
    db.flush()
    staff_a = User(full_name="Master A", email="staff.a@example.com", hashed_password="synthetic",
                   role="business_owner", business_id=a.id)
    staff_b = User(full_name="Master B", email="staff.b@example.com", hashed_password="synthetic",
                   role="business_owner", business_id=b.id)
    db.add_all([staff_a, staff_b])
    db.flush()
    client = Client(full_name="Shared global card", contact_email=one.email_canonical,
                    owner_id=staff_a.id, contact_phone="private-phone-canary", access_password="private-QR-canary")
    other = Client(full_name="Other card", contact_email=one.email_canonical, owner_id=staff_b.id)
    db.add_all([client, other])
    db.flush()
    db.add_all([ClientBusinessRelation(client_id=client.id, business_id=a.id),
                ClientBusinessRelation(client_id=client.id, business_id=b.id)])
    result = {"one": one.id, "two": two.id, "a": a.id, "b": b.id,
              "staff_a": staff_a.id, "staff_b": staff_b.id, "client": client.id, "other_client": other.id}
    result["a1"] = add_visit(db, result, start=NOW + timedelta(days=1))
    result["a2"] = add_visit(db, result, start=NOW - timedelta(days=1), status="completed")
    result["b1"] = add_visit(db, result, business="b", start=NOW + timedelta(days=2))
    result["a_other"] = add_visit(db, result, client="other_client", start=NOW + timedelta(days=3))
    result["solo"] = add_visit(db, result, business=None, start=NOW + timedelta(days=4))
    db.commit()
    return result


def ids(page):
    return [entry.id for entry in page.entries]


def test_contact_email_crm_owner_and_membership_do_not_grant_any_access(db, world):
    assert history.list_visits(db, world["one"]).entries == ()
    for key in ("a1", "a2", "b1", "a_other", "solo"):
        assert history.get_visit(db, world["one"], world[key]) is None
    assert history.list_visits(db, world["one"] + 1000).entries == ()


def test_visit_grant_opens_one_visit_not_neighbor_or_other_salon(db, world):
    grant(db, world, visit=world["a1"])
    db.commit()
    assert ids(history.list_visits(db, world["one"])) == [world["a1"]]
    assert history.get_visit(db, world["one"], world["a1"]).id == world["a1"]
    assert history.get_visit(db, world["one"], world["a2"]) is None
    assert history.get_visit(db, world["two"], world["a1"]) is None
    assert history.get_visit(db, world["one"], 987654321) is None


def test_client_grant_is_fixed_to_business_and_global_client_even_with_two_actors(db, world):
    grant(db, world)
    grant(db, world, actor="two", business="b")
    db.commit()
    assert ids(history.list_visits(db, world["one"])) == [world["a1"], world["a2"]]
    assert ids(history.list_visits(db, world["two"])) == [world["b1"]]
    assert history.list_visits(db, world["one"], business_id=world["b"]).entries == ()
    assert ids(history.list_visits(db, world["one"], business_id=world["a"])) == [world["a1"], world["a2"]]


def test_overlapping_client_and_visit_grants_do_not_duplicate_or_distort_pages(db, world):
    grant(db, world)
    grant(db, world, visit=world["a1"])
    db.commit()
    first = history.list_visits(db, world["one"], limit=1)
    assert ids(first) == [world["a1"]] and first.next_cursor is not None
    last = history.list_visits(db, world["one"], limit=1, cursor=first.next_cursor)
    assert ids(last) == [world["a2"]] and last.next_cursor is None


def test_revocation_and_replacement_owner_do_not_resurrect_old_access(db, world):
    original = grant(db, world, visit=world["a1"])
    db.commit()
    assert history.get_visit(db, world["one"], world["a1"]) is not None
    db.execute(update(VisitorGrant).where(VisitorGrant.id == original).values(revoked_at=NOW))
    grant(db, world, actor="two", visit=world["a1"])
    db.commit()
    assert history.get_visit(db, world["one"], world["a1"]) is None
    assert history.list_visits(db, world["one"]).entries == ()
    assert history.get_visit(db, world["two"], world["a1"]) is not None


@pytest.mark.parametrize("wrong", ["business", "client"])
def test_malformed_fixed_scope_does_not_resolve_target(db, world, wrong):
    grant(db, world, business="b" if wrong == "business" else "a",
          client="other_client" if wrong == "client" else "client", visit=world["a1"])
    db.commit()
    assert history.get_visit(db, world["one"], world["a1"]) is None
    assert history.list_visits(db, world["one"]).entries == ()


@pytest.mark.parametrize("target", ["client_id", "business_id"])
def test_a_reassigned_visit_cannot_keep_its_old_visit_grant(db, world, target):
    grant(db, world, visit=world["a1"])
    db.commit()
    assert history.get_visit(db, world["one"], world["a1"]) is not None
    db.execute(update(Visit).where(Visit.id == world["a1"]).values({target: world["other_client" if target == "client_id" else "b"]}))
    db.commit()
    assert history.get_visit(db, world["one"], world["a1"]) is None
    assert history.list_visits(db, world["one"]).entries == ()


def test_mutable_staff_owner_membership_and_contact_do_not_expand_or_hide_fixed_history(db, world):
    grant(db, world)
    db.commit()
    db.execute(update(User).where(User.id == world["staff_a"]).values(business_id=world["b"]))
    db.execute(update(Client).where(Client.id == world["client"]).values(
        owner_id=world["staff_b"], contact_email="other@example.com"))
    db.execute(delete(ClientBusinessRelation).where(ClientBusinessRelation.client_id == world["client"]))
    db.commit()
    assert ids(history.list_visits(db, world["one"])) == [world["a1"], world["a2"]]
    assert history.list_visits(db, world["two"]).entries == ()
    assert history.get_visit(db, world["one"], world["solo"]) is None


@pytest.mark.parametrize("target", ["business", "client", "visit"])
def test_soft_deleted_targets_fail_closed_without_substitution(db, world, target):
    grant(db, world)
    grant(db, world, business="b", visit=world["b1"])
    db.commit()
    model, key = {"business": (Business, "a"), "client": (Client, "client"), "visit": (Visit, "a1")}[target]
    db.execute(update(model).where(model.id == world[key]).values(deleted_at=NOW))
    db.commit()
    assert history.get_visit(db, world["one"], world["a1"]) is None
    expected = [] if target == "client" else [world["b1"]] + ([world["a2"]] if target == "visit" else [])
    assert ids(history.list_visits(db, world["one"])) == expected


def test_archive_keeps_history_and_nullable_end_is_not_invented(db, world):
    grant(db, world)
    db.execute(update(Client).where(Client.id == world["client"]).values(archived_at=NOW))
    db.execute(update(Visit).where(Visit.id == world["a2"]).values(archived_at=NOW, date_end=None))
    db.commit()
    entry = history.get_visit(db, world["one"], world["a2"])
    assert entry.date_end is None and entry.status == "completed"
    assert ids(history.list_visits(db, world["one"])) == [world["a1"], world["a2"]]


def test_hard_deleted_visit_is_unavailable_and_cascaded_grant_is_not_reused(db, world):
    grant(db, world, visit=world["a1"])
    db.commit()
    db.delete(db.get(Visit, world["a1"]))
    db.commit()
    assert history.get_visit(db, world["one"], world["a1"]) is None
    assert history.list_visits(db, world["one"]).entries == ()
    assert db.query(VisitorGrant).count() == 0


def test_unpaid_disabled_booking_and_mail_do_not_gate_history_or_run_side_effect_helpers(db, world, monkeypatch):
    from app.api.core import service_payment_service, notification_events, waitlist_service
    from app.api.core.config import settings
    grant(db, world)
    db.add(PublicBookingSettings(business_id=world["a"], slug="salon-a", is_enabled=False))
    db.commit()
    monkeypatch.setattr(settings, "visitor_auth_mail_mode", "disabled")
    def forbidden(*_args, **_kwargs):
        pytest.fail("History called a subscription/token/booking/payment/notification mutation helper")
    for module, name in [(subscription_access, "enforce_business_access"),
        (subscription_access, "get_subscription_access_state"), (booking_manage_access, "resolve_token"),
        (booking_manage_access, "issue_token"), (public_booking_service, "submit_booking"),
        (service_payment_service, "expire_stale_holds"), (notification_events, "queue_booking_created"),
        (waitlist_service, "release_slot_quietly")]:
        monkeypatch.setattr(module, name, forbidden)
    assert ids(history.list_visits(db, world["one"])) == [world["a1"], world["a2"]]
    assert history.get_visit(db, world["one"], world["a1"]) is not None


def test_more_than_two_hundred_rows_equal_times_and_insertions_use_stable_keyset(db, world):
    grant(db, world)
    extra = [add_visit(db, world, start=NOW + timedelta(hours=n // 7), status="cancelled") for n in range(211)]
    foreign = [add_visit(db, world, business="b", start=NOW + timedelta(days=10), status="cancelled") for _ in range(3)]
    db.commit()
    expected = [row.id for row in db.query(Visit.id).filter(Visit.id.in_([world["a1"], world["a2"], *extra]))
                .order_by(Visit.date_start.desc(), Visit.id.desc())]
    assert len(history.list_visits(db, world["one"]).entries) == 25
    assert len(history.list_visits(db, world["one"], limit=100).entries) == 100
    first = history.list_visits(db, world["one"], limit=17)
    received = ids(first)
    assert received == expected[:17]
    newer = add_visit(db, world, start=NOW + timedelta(days=100))
    db.commit()
    cursor = first.next_cursor
    while cursor is not None:
        page = history.list_visits(db, world["one"], limit=17, cursor=cursor)
        received.extend(ids(page))
        cursor = page.next_cursor
    assert received == expected and len(set(received)) == 213
    assert newer not in received and not set(foreign).intersection(received)
    assert history.list_visits(db, world["one"], cursor=history.VisitorHistoryCursor(NOW - timedelta(days=100), 1)).entries == ()


def test_foreign_cursor_is_only_a_boundary_and_cannot_grant_foreign_details(db, world):
    grant(db, world)
    grant(db, world, actor="two", business="b")
    db.commit()
    foreign = history.get_visit(db, world["two"], world["b1"])
    page = history.list_visits(db, world["one"], cursor=history.VisitorHistoryCursor(foreign.date_start, foreign.id))
    assert ids(page) == [world["a1"], world["a2"]]
    assert history.get_visit(db, world["one"], foreign.id) is None


def test_output_is_immutable_allowlist_without_orm_financial_or_client_data(db, world):
    grant(db, world)
    db.commit()
    page = history.list_visits(db, world["one"], limit=1)
    assert isinstance(page.entries, tuple)
    entry = page.entries[0]
    assert {field.name for field in fields(entry)} == {"id", "business_id", "date_start", "date_end", "status"}
    assert not hasattr(entry, "_sa_instance_state")
    for private in ("private-note-canary", "private-photo-canary", "private-phone-canary", "private-QR-canary", "1234.56"):
        assert private not in repr(asdict(page))
    with pytest.raises(FrozenInstanceError):
        entry.status = "completed"
    with pytest.raises(FrozenInstanceError):
        page.next_cursor = None
    with pytest.raises(FrozenInstanceError):
        page.next_cursor.id = 1


def test_queries_are_single_select_and_never_flush_caller_dirty_or_new_rows(db, world, monkeypatch):
    grant(db, world)
    db.commit()
    dirty = db.get(Client, world["client"])
    dirty.contact_phone = "pending-private-edit"
    new = User(full_name="Pending unrelated", email="pending@example.com", hashed_password="synthetic")
    db.add(new)
    statements = []
    def record(_conn, _cursor, statement, *_args):
        statements.append(statement)
    def forbidden(*_args, **_kwargs):
        pytest.fail("History flushed or committed the caller's transaction")
    monkeypatch.setattr(db, "flush", forbidden)
    monkeypatch.setattr(db, "commit", forbidden)
    event.listen(db.get_bind(), "before_cursor_execute", record)
    try:
        assert ids(history.list_visits(db, world["one"])) == [world["a1"], world["a2"]]
        assert history.get_visit(db, world["one"], world["a1"]) is not None
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", record)
    assert len(statements) == 2
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    assert all("EXISTS" in statement and "OFFSET" not in statement for statement in statements)
    assert new.id is None and dirty in db.dirty and new in db.new
    with Session(db.get_bind()) as observer:
        assert observer.get(Client, world["client"]).contact_phone == "private-phone-canary"
        assert observer.query(User).filter_by(email="pending@example.com").count() == 0


@pytest.mark.parametrize("value", [None, True, False, 0, -1, "1", 1.5])
def test_account_ids_are_strictly_validated_before_query(db, value, monkeypatch):
    monkeypatch.setattr(db, "execute", lambda *_args, **_kwargs: pytest.fail("bad actor reached SQL"))
    with pytest.raises(ValueError):
        history.list_visits(db, value)
    with pytest.raises(ValueError):
        history.get_visit(db, value, 1)


@pytest.mark.parametrize("limit", [True, None, 0, -1, 101, "25", 2.5])
def test_bad_page_limits_never_reach_sql(db, limit, monkeypatch):
    monkeypatch.setattr(db, "execute", lambda *_args, **_kwargs: pytest.fail("bad limit reached SQL"))
    with pytest.raises(ValueError):
        history.list_visits(db, 1, limit=limit)


@pytest.mark.parametrize("value", [True, 0, -1, "1", 1.5])
def test_bad_business_and_visit_ids_never_reach_sql(db, value, monkeypatch):
    monkeypatch.setattr(db, "execute", lambda *_args, **_kwargs: pytest.fail("bad ID reached SQL"))
    with pytest.raises(ValueError):
        history.list_visits(db, 1, business_id=value)
    with pytest.raises(ValueError):
        history.get_visit(db, 1, value)


@pytest.mark.parametrize("cursor", [{"date_start": NOW, "id": 1}, (NOW, 1), "opaque-token", True])
def test_untyped_cursor_never_reaches_sql(db, cursor, monkeypatch):
    monkeypatch.setattr(db, "execute", lambda *_args, **_kwargs: pytest.fail("bad cursor reached SQL"))
    with pytest.raises(ValueError):
        history.list_visits(db, 1, cursor=cursor)


@pytest.mark.parametrize("start,visit_id", [("2026-10-26", 1), (None, 1), (NOW, True), (NOW, 0), (NOW, "1")])
def test_cursor_requires_datetime_and_positive_integer_id(start, visit_id):
    with pytest.raises(ValueError):
        history.VisitorHistoryCursor(start, visit_id)


def test_aware_cursor_is_normalized_to_the_same_utc_boundary(db, world):
    grant(db, world)
    db.commit()
    naive = history.VisitorHistoryCursor(NOW + timedelta(days=1), world["a1"])
    aware = history.VisitorHistoryCursor((NOW + timedelta(days=1, hours=2)).replace(tzinfo=timezone(timedelta(hours=2))), world["a1"])
    assert naive == aware
    assert ids(history.list_visits(db, world["one"], cursor=aware)) == [world["a2"]]


@pytest.mark.parametrize("status", [None, ""])
def test_nullable_or_empty_stored_status_is_preserved_without_inventing_planned(db, world, status):
    grant(db, world)
    db.commit()
    # SQL bypasses the ORM insertion default so this is a real legacy NULL/empty value.
    db.execute(update(Visit).where(Visit.id == world["a2"]).values(status=status))
    db.commit()
    assert db.execute(select(Visit.status).where(Visit.id == world["a2"])).scalar_one() == status
    detail = history.get_visit(db, world["one"], world["a2"])
    assert detail is not None and detail.status == status
    entry = next(row for row in history.list_visits(db, world["one"]).entries if row.id == world["a2"])
    assert entry.status == status
