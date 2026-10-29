"""Session lifecycle against the real migrated throwaway PostgreSQL database."""
import base64
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Event

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.api.core import visitor_sessions
from app.api.core.security import create_access_token, create_refresh_token, get_password_hash, verify_password
from app.db.models.visitor_account import VisitorAccount
from app.db.models.visitor_auth import VisitorSession

NOW = datetime(2026, 10, 25, 8, 0, 0, 123456)


@pytest.fixture(autouse=True)
def fixed_session_clock(monkeypatch):
    monkeypatch.setattr(visitor_sessions, "utcnow", lambda: NOW)


def account(db, email="first.visitor@example.com"):
    row = VisitorAccount(email_canonical=email, email_verified_at=NOW)
    db.add(row)
    db.commit()
    return row


def issued(db, owner):
    row, raw = visitor_sessions.create_session(db, owner)
    db.commit()
    return row, raw


def test_issue_uses_its_namespace_and_stores_only_a_digest(db):
    owner = account(db)
    row, raw = issued(db, owner)
    assert raw.startswith("vst_") and len(raw) == 47
    assert len(base64.urlsafe_b64decode(raw[4:] + "=")) == 32
    assert row.account_id == owner.id
    assert row.token_hash == hashlib.sha256(raw.encode("ascii")).hexdigest()
    assert row.token_hash != raw and row.created_at == NOW
    assert row.expires_at == NOW + timedelta(days=30) and row.revoked_at is None
    stored = db.execute(select(VisitorSession.__table__)).mappings().one()
    assert raw not in stored.values()
    assert {"raw_token", "token", "credential", "password"}.isdisjoint(stored)


def test_issue_calls_the_csprng_with_32_bytes_and_does_not_reuse_tokens(db, monkeypatch):
    owner = account(db)
    calls = []
    real = visitor_sessions.secrets.token_urlsafe
    def capture(size):
        calls.append(size)
        return real(size)
    monkeypatch.setattr(visitor_sessions.secrets, "token_urlsafe", capture)
    first, first_raw = issued(db, owner)
    second, second_raw = issued(db, owner)
    assert calls == [32, 32]
    assert first_raw != second_raw and first.token_hash != second.token_hash


def test_issue_flush_is_invisible_elsewhere_and_rollback_removes_it(db):
    owner = account(db)
    row, raw = visitor_sessions.create_session(db, owner)
    row_id = row.id
    assert inspect(row).persistent
    assert visitor_sessions.get_valid_session(db, raw).id == row_id
    with Session(db.get_bind()) as other:
        assert other.get(VisitorSession, row_id) is None
    db.rollback()
    assert db.get(VisitorSession, row_id) is None
    assert visitor_sessions.get_valid_session(db, raw) is None


def test_account_and_session_creation_can_roll_back_in_one_transaction(db):
    owner = VisitorAccount(email_canonical="atomic.visitor@example.com", email_verified_at=NOW)
    db.add(owner)
    db.flush()
    owner_id = owner.id
    row, raw = visitor_sessions.create_session(db, owner)
    assert row.account_id == owner_id
    db.rollback()
    assert db.get(VisitorAccount, owner_id) is None
    assert visitor_sessions.get_valid_session(db, raw) is None


def test_factory_rejects_transient_pending_detached_and_unverified_objects(db):
    transient = VisitorAccount(email_canonical="transient@example.com", email_verified_at=NOW)
    with pytest.raises(ValueError):
        visitor_sessions.create_session(db, transient)
    db.add(transient)
    with pytest.raises(ValueError):
        visitor_sessions.create_session(db, transient)
    db.rollback()
    owner = account(db)
    owner_id = owner.id
    db.expunge(owner)
    with pytest.raises(ValueError):
        visitor_sessions.create_session(db, owner)
    owner = db.get(VisitorAccount, owner_id)
    owner.email_verified_at = None
    with pytest.raises(ValueError):
        visitor_sessions.create_session(db, owner)
    db.rollback()
    assert db.query(VisitorSession).count() == 0


def test_factory_rejects_a_different_session_and_a_crm_user(db, make_user):
    crm_user = make_user()
    owner = account(db)
    with pytest.raises(ValueError):
        visitor_sessions.create_session(db, crm_user)
    with Session(db.get_bind()) as other:
        with pytest.raises(ValueError):
            visitor_sessions.create_session(other, owner)


@pytest.mark.parametrize("expired_object", [False, True])
def test_factory_rechecks_a_deleted_account_instead_of_trusting_its_cached_object(db, expired_object):
    owner = account(db)
    owner_id = owner.id
    assert owner.email_verified_at == NOW
    if expired_object:
        db.expire(owner)
    with Session(db.get_bind()) as other:
        other.execute(text("DELETE FROM visitor_accounts WHERE id=:id"), {"id": owner_id})
        other.commit()
    with pytest.raises(ValueError):
        visitor_sessions.create_session(db, owner)
    db.rollback()
    assert db.query(VisitorSession).count() == 0


def test_expiry_is_exclusive_and_reads_never_slide_it(db, monkeypatch):
    row, raw = issued(db, account(db))
    deadline = row.expires_at
    monkeypatch.setattr(visitor_sessions, "utcnow", lambda: deadline - timedelta(microseconds=1))
    assert visitor_sessions.get_valid_session(db, raw).expires_at == deadline
    assert visitor_sessions.get_valid_session(db, raw).expires_at == deadline
    monkeypatch.setattr(visitor_sessions, "utcnow", lambda: deadline)
    assert visitor_sessions.get_valid_session(db, raw) is None
    monkeypatch.setattr(visitor_sessions, "utcnow", lambda: deadline + timedelta(microseconds=1))
    assert visitor_sessions.get_valid_session(db, raw) is None
    db.refresh(row)
    assert row.expires_at == deadline


@pytest.mark.parametrize("invalid", [None, "", "vst_", "vst_" + "a" * 42,
    "vst_" + "a" * 44, "VST_" + "a" * 43, "vst_" + "!" * 43,
    "vst_" + "é" * 43, "vst_" + "a" * 43 + "\n", " vst_" + "a" * 43])
def test_resolver_rejects_malformed_credentials(db, invalid):
    assert visitor_sessions.get_valid_session(db, invalid) is None


def test_resolver_rejects_tampering_and_raw_digest_as_credential(db):
    row, raw = issued(db, account(db))
    suffix = "A" if raw[-1] != "A" else "B"
    assert visitor_sessions.get_valid_session(db, raw[:-1] + suffix) is None
    assert visitor_sessions.get_valid_session(db, raw[4:]) is None
    assert visitor_sessions.get_valid_session(db, row.token_hash) is None
    assert visitor_sessions.get_valid_session(db, "vst_" + "a" * 43) is None


def test_external_revocation_is_seen_even_with_a_cached_valid_session(db):
    row, raw = issued(db, account(db))
    cached = visitor_sessions.get_valid_session(db, raw)
    assert cached.revoked_at is None
    with Session(db.get_bind()) as other:
        other.query(VisitorSession).filter(VisitorSession.id == row.id).update(
            {"revoked_at": NOW + timedelta(seconds=1)})
        other.commit()
    assert visitor_sessions.get_valid_session(db, raw) is None


def test_valid_lookup_refreshes_cached_expiry_state(db):
    row, raw = issued(db, account(db))
    cached = visitor_sessions.get_valid_session(db, raw)
    shortened = NOW + timedelta(hours=1)
    with Session(db.get_bind()) as other:
        other.query(VisitorSession).filter(VisitorSession.id == row.id).update({"expires_at": shortened})
        other.commit()
    current = visitor_sessions.get_valid_session(db, raw)
    assert current is cached and current.expires_at == shortened


def test_duplicate_entropy_is_stopped_by_the_database_without_moving_the_session(db, monkeypatch):
    first = account(db)
    second = account(db, "second.visitor@example.com")
    monkeypatch.setattr(visitor_sessions.secrets, "token_urlsafe", lambda _: "A" * 43)
    row, raw = issued(db, first)
    original_id = row.id
    with pytest.raises(IntegrityError):
        visitor_sessions.create_session(db, second)
    db.rollback()
    assert db.query(VisitorSession).count() == 1
    assert visitor_sessions.get_valid_session(db, raw).id == original_id
    assert visitor_sessions.get_valid_session(db, raw).account_id == first.id


def test_logout_one_is_owned_idempotent_and_rollbackable(db, monkeypatch):
    first, second = account(db), account(db, "second.visitor@example.com")
    row, raw = issued(db, first)
    sibling, sibling_raw = issued(db, first)
    foreign, foreign_raw = issued(db, second)
    assert visitor_sessions.revoke_session(db, account_id=first.id, session_id=foreign.id) is False
    assert visitor_sessions.revoke_session(db, account_id=first.id, session_id=row.id) is True
    assert visitor_sessions.get_valid_session(db, raw) is None
    db.rollback()
    assert visitor_sessions.get_valid_session(db, raw) is not None
    assert visitor_sessions.revoke_session(db, account_id=first.id, session_id=row.id) is True
    db.commit()
    monkeypatch.setattr(visitor_sessions, "utcnow", lambda: NOW + timedelta(minutes=1))
    assert visitor_sessions.revoke_session(db, account_id=first.id, session_id=row.id) is False
    db.commit()
    db.refresh(row)
    assert row.revoked_at == NOW
    assert visitor_sessions.get_valid_session(db, sibling_raw).id == sibling.id
    assert visitor_sessions.get_valid_session(db, foreign_raw).id == foreign.id


def test_logout_all_and_password_change_share_the_callers_transaction(db):
    first, second = account(db), account(db, "second.visitor@example.com")
    first.hashed_password = get_password_hash("Old!Visitor-pass-26")
    db.commit()
    first_id = first.id
    _, first_raw = issued(db, first)
    _, sibling_raw = issued(db, first)
    foreign, foreign_raw = issued(db, second)
    assert visitor_sessions.revoke_all_sessions(db, account_id=first_id) == 2
    first.hashed_password = get_password_hash("New!Visitor-pass-26")
    db.flush()
    db.rollback()
    assert verify_password("Old!Visitor-pass-26", first.hashed_password)
    assert visitor_sessions.get_valid_session(db, first_raw) is not None
    assert visitor_sessions.get_valid_session(db, sibling_raw) is not None
    assert visitor_sessions.revoke_all_sessions(db, account_id=first_id) == 2
    first.hashed_password = get_password_hash("New!Visitor-pass-26")
    db.commit()
    assert verify_password("New!Visitor-pass-26", first.hashed_password)
    assert visitor_sessions.get_valid_session(db, first_raw) is None
    assert visitor_sessions.get_valid_session(db, sibling_raw) is None
    assert visitor_sessions.get_valid_session(db, foreign_raw).id == foreign.id
    assert visitor_sessions.revoke_all_sessions(db, account_id=first_id) == 0


@pytest.mark.parametrize("invalid", [None, 0, -1, True, "1"])
def test_missing_account_scope_cannot_become_a_global_logout(db, invalid):
    row, raw = issued(db, account(db))
    with pytest.raises(ValueError):
        visitor_sessions.revoke_all_sessions(db, account_id=invalid)
    assert visitor_sessions.get_valid_session(db, raw).id == row.id


def test_logout_all_uses_the_same_account_lock_as_an_uncommitted_issue(db):
    owner = account(db)
    owner_id = owner.id
    _, raw = visitor_sessions.create_session(db, owner)
    with Session(db.get_bind()) as other:
        other.execute(text("SET LOCAL lock_timeout = '200ms'"))
        with pytest.raises(OperationalError) as caught:
            visitor_sessions.revoke_all_sessions(other, account_id=owner_id)
        assert caught.value.orig.pgcode == "55P03"
        other.rollback()
    db.commit()
    assert visitor_sessions.revoke_all_sessions(db, account_id=owner_id) == 1
    db.commit()
    assert visitor_sessions.get_valid_session(db, raw) is None


def test_login_rechecks_password_after_reset_releases_the_account_lock(db):
    owner = account(db)
    owner.hashed_password = get_password_hash("Old!Visitor-pass-26")
    db.commit()
    _, old_raw = issued(db, owner)
    owner_id = owner.id
    visitor_sessions.lock_verified_account(db, owner_id)
    assert visitor_sessions.revoke_all_sessions(db, account_id=owner_id) == 1
    owner.hashed_password = get_password_hash("New!Visitor-pass-26")
    db.flush()
    preflight_done = Event()
    bind = db.get_bind()

    def login_after_nonlocking_preflight():
        with Session(bind) as other:
            cached = other.get(VisitorAccount, owner_id)
            # A nonlocking read can still see the old committed hash. It is not authentication.
            assert verify_password("Old!Visitor-pass-26", cached.hashed_password)
            preflight_done.set()
            current = visitor_sessions.lock_verified_account(other, owner_id)
            assert current is cached
            if not verify_password("Old!Visitor-pass-26", current.hashed_password):
                other.rollback()
                return "rejected"
            visitor_sessions.create_session(other, current)
            other.commit()
            return "issued"

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(login_after_nonlocking_preflight)
        try:
            assert preflight_done.wait(timeout=5)
            db.commit()
        finally:
            if db.in_transaction():
                db.rollback()
        assert pending.result(timeout=10) == "rejected"
    assert visitor_sessions.get_valid_session(db, old_raw) is None
    assert db.query(VisitorSession).filter(VisitorSession.account_id == owner_id,
        VisitorSession.revoked_at.is_(None)).count() == 0


def test_real_crm_and_legacy_factories_do_not_produce_visitor_credentials(db, make_user):
    from app.api.core import booking_manage_access
    from app.api.crud import crud_client_portal, crud_password_reset
    from app.db.models.business import Business
    from app.db.models.client import Client
    from app.db.models.client_business_relation import ClientBusinessRelation
    from app.db.models.visit import Visit

    crm = make_user("business_owner")
    business = Business(name="Credential Boundary", business_type="salon")
    guest = Client(full_name="Credential Boundary Guest", owner_id=crm.id)
    db.add_all([business, guest])
    db.flush()
    db.add(ClientBusinessRelation(business_id=business.id, client_id=guest.id))
    visit = Visit(client_id=guest.id, specialist_id=crm.id, business_id=business.id,
                  date_start=NOW + timedelta(days=2), date_end=NOW + timedelta(days=2, hours=1))
    db.add(visit)
    db.commit()
    portal, portal_raw = crud_client_portal.create_session(db, business.id, guest.id)
    db.commit()
    magic_raw = crud_client_portal.issue_magic_token(db, business.id, guest.id)
    manage_raw, manage = booking_manage_access.issue_token(db, visit)
    reset, reset_raw = crud_password_reset.issue_token(db, crm.id)
    crm_access = create_access_token({"sub": str(crm.id), "role": crm.role})
    crm_refresh, _ = create_refresh_token({"sub": str(crm.id), "role": crm.role})
    row, visitor_raw = issued(db, account(db))
    for other in [portal_raw, magic_raw, manage_raw, reset_raw, crm_access, crm_refresh]:
        assert visitor_sessions.get_valid_session(db, other) is None
        assert visitor_sessions.get_valid_session(db, "vst_" + other) is None
    assert visitor_sessions.get_valid_session(db, visitor_raw).id == row.id
    assert crud_client_portal.get_valid_session(db, visitor_raw) is None
    db.refresh(portal)
    db.refresh(manage)
    db.refresh(reset)
    assert portal.revoked_at is None and manage.revoked_at is None and reset.used_at is None


@pytest.mark.parametrize('operation', ['issue', 'one', 'all'])
def test_session_operations_preserve_pending_password_changes(db, operation):
    owner = account(db)
    owner.hashed_password = get_password_hash('Old!Visitor-pass-26')
    db.commit()
    existing, _ = issued(db, owner)
    owner_id, session_id = owner.id, existing.id
    current = visitor_sessions.lock_verified_account(db, owner_id)
    current.hashed_password = get_password_hash('New!Visitor-pass-26')
    if operation == 'issue':
        visitor_sessions.create_session(db, current)
    elif operation == 'one':
        visitor_sessions.revoke_session(db, account_id=owner_id, session_id=session_id)
    else:
        visitor_sessions.revoke_all_sessions(db, account_id=owner_id)
    db.commit()
    db.expire_all()
    assert verify_password('New!Visitor-pass-26', db.get(VisitorAccount, owner_id).hashed_password)


def test_fresh_auth_lock_refuses_pending_account_mutations_without_discarding_them(db):
    owner = account(db)
    owner_id = owner.id
    owner.hashed_password = 'pending-hash-for-a-later-operation'
    with pytest.raises(ValueError):
        visitor_sessions.lock_verified_account(db, owner_id)
    assert owner.hashed_password == 'pending-hash-for-a-later-operation'
    db.rollback()
