"""Access schema constraints and FK actions on real PostgreSQL, without claim services."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import hashlib
from threading import Barrier

import pytest
from sqlalchemy import delete, insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import models
from app.db.models.booking_manage_token import BookingManageToken
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.client_business_relation import ClientBusinessRelation
from app.db.models.user import User
from app.db.models.visit import Visit
from app.db.models.visitor_access import VisitorClaim, VisitorGrant
from app.db.models.visitor_account import VisitorAccount

NOW = datetime(2026, 10, 26, 12)


def seed_access_world(db):
    one = VisitorAccount(email_canonical="owner.one@example.com", email_verified_at=NOW)
    two = VisitorAccount(email_canonical="owner.two@example.com", email_verified_at=NOW)
    business = Business(name="Scope A", business_type="salon")
    other = Business(name="Scope B", business_type="salon")
    db.add_all([one, two, business, other])
    db.flush()
    staff = User(full_name="Staff", email="staff@example.com", hashed_password="synthetic", business_id=business.id)
    issuer = User(full_name="Issuer", email="issuer@example.com", hashed_password="synthetic", business_id=business.id)
    db.add_all([staff, issuer])
    db.flush()
    client = Client(full_name="Shared CRM card", owner_id=staff.id, contact_email=one.email_canonical)
    db.add(client)
    db.flush()
    db.add_all([ClientBusinessRelation(client_id=client.id, business_id=business.id),
                ClientBusinessRelation(client_id=client.id, business_id=other.id)])
    visit = Visit(client_id=client.id, specialist_id=staff.id, business_id=business.id,
                  date_start=NOW, date_end=NOW + timedelta(hours=1), status="planned")
    db.add(visit)
    db.flush()
    token = BookingManageToken(business_id=business.id, visit_id=visit.id, token_hash="c" * 64,
                               created_at=NOW, expires_at=NOW + timedelta(days=7))
    db.add(token)
    db.flush()
    result = {"one": one.id, "two": two.id, "business": business.id, "other": other.id,
              "staff": staff.id, "issuer": issuer.id, "client": client.id, "visit": visit.id, "token": token.id}
    db.commit()
    return result


@pytest.fixture
def access_world(db):
    return seed_access_world(db)


def grant_data(world, scope="visit", **changes):
    return {"account_id": world["one"], "business_id": world["business"], "client_id": world["client"],
            "scope": scope, "visit_id": world["visit"] if scope == "visit" else None,
            "created_at": NOW, **changes}


def claim_data(world, kind="client_invite", **changes):
    return {"kind": kind, "proof_hash": ("a" if kind == "client_invite" else "c") * 64,
            "business_id": world["business"], "client_id": world["client"],
            "visit_id": world["visit"] if kind == "manage_visit" else None,
            "recipient_email": "recipient@example.com" if kind == "client_invite" else None,
            "source_manage_token_id": world["token"] if kind == "manage_visit" else None,
            "issued_by_user_id": world["issuer"] if kind == "client_invite" else None,
            "created_at": NOW, "expires_at": NOW + timedelta(minutes=30),
            "consumed_at": NOW if kind == "manage_visit" else None,
            "consumed_account_id": world["one"] if kind == "manage_visit" else None, **changes}


def rejected(db, table, values):
    with pytest.raises(IntegrityError) as failure:
        with db.begin_nested():
            db.execute(insert(table).values(**values))
    return failure.value.orig.diag


def test_registry_and_invite_before_account_keep_only_digest_and_canonical_recipient(db, access_world):
    assert models.VisitorGrant is VisitorGrant and models.VisitorClaim is VisitorClaim
    raw = "vci_synthetic-private-proof-never-store-the-raw-value"
    row = VisitorClaim(**claim_data(access_world, proof_hash=hashlib.sha256(raw.encode()).hexdigest(),
                                    recipient_email="  New.Recipient+Tag@EXAMPLE.COM  "))
    before_accounts, before_users = db.query(VisitorAccount).count(), db.query(User).count()
    db.add(row)
    db.commit()
    assert row.recipient_email == "new.recipient+tag@example.com"
    assert row.consumed_at is None and row.consumed_account_id is None
    assert db.query(VisitorAccount).count() == before_accounts and db.query(User).count() == before_users
    assert not db.query(VisitorAccount.id).filter_by(email_canonical=row.recipient_email).first()
    assert raw not in [getattr(row, column.name) for column in row.__table__.columns]


@pytest.mark.parametrize("changes", [{"scope": "business"}, {"scope": None}, {"visit_id": None},
    {"scope": "client", "visit_id": 1}, {"account_id": None}, {"business_id": None}, {"client_id": None}])
def test_grant_scope_and_required_identity_are_database_constraints(db, access_world, changes):
    changes = dict(changes)
    if changes.get("visit_id") is not None:
        changes["visit_id"] = access_world["visit"]
    diag = rejected(db, VisitorGrant, grant_data(access_world, **changes))
    assert diag.constraint_name is None or diag.constraint_name.startswith("ck_visitor_grants_")


@pytest.mark.parametrize("changes", [{"kind": "portal"}, {"kind": None}, {"proof_hash": "A" * 64},
    {"proof_hash": "short"}, {"proof_hash": "vci_raw-secret"}, {"proof_hash": None},
    {"recipient_email": ""}, {"recipient_email": " recipient@example.com "}, {"recipient_email": None},
    {"visit_id": 1}, {"source_manage_token_id": 1}, {"expires_at": NOW},
    {"consumed_account_id": 1}, {"grant_id": 1}])
def test_invite_matrix_hash_expiry_and_consumption_are_database_constraints(db, access_world, changes):
    changes = dict(changes)
    if "grant_id" in changes:
        grant = VisitorGrant(**grant_data(access_world))
        db.add(grant)
        db.flush()
        changes["grant_id"] = grant.id
    for field, key in [("visit_id", "visit"), ("source_manage_token_id", "token"), ("consumed_account_id", "one")]:
        if changes.get(field) is not None:
            changes[field] = access_world[key]
    diag = rejected(db, VisitorClaim, claim_data(access_world, **changes))
    assert diag.constraint_name is None or diag.constraint_name.startswith("ck_visitor_claims_")


@pytest.mark.parametrize("changes", [{"recipient_email": "recipient@example.com"}, {"visit_id": None},
                                      {"issued_by_user_id": 1}, {"consumed_at": None}])
def test_manage_receipt_cannot_be_an_invite_or_pending_proof(db, access_world, changes):
    changes = dict(changes)
    if "issued_by_user_id" in changes:
        changes["issued_by_user_id"] = access_world["issuer"]
    if "consumed_at" in changes:
        changes["consumed_account_id"] = None
    diag = rejected(db, VisitorClaim, claim_data(access_world, "manage_visit", **changes))
    assert diag.constraint_name == "ck_visitor_claims_scope"


@pytest.mark.parametrize("scope", ["visit", "client"])
def test_active_target_is_exclusive_between_accounts_but_revocation_allows_a_new_grant(db, access_world, scope):
    original = VisitorGrant(**grant_data(access_world, scope))
    db.add(original)
    db.commit()
    rejected(db, VisitorGrant, grant_data(access_world, scope, account_id=access_world["two"]))
    original.revoked_at = NOW + timedelta(seconds=1)
    db.commit()
    replacement = VisitorGrant(**grant_data(access_world, scope, account_id=access_world["two"]))
    db.add(replacement)
    db.commit()
    assert db.query(VisitorGrant).count() == 2
    assert replacement.account_id != original.account_id and original.revoked_at is not None


@pytest.mark.parametrize("scope", ["visit", "client"])
def test_real_unique_race_between_accounts_has_exactly_one_winner(db, access_world, scope):
    engine, barrier = db.get_bind(), Barrier(2)
    def attempt(account_id):
        with Session(engine) as session:
            session.add(VisitorGrant(**grant_data(access_world, scope, account_id=account_id)))
            barrier.wait(timeout=5)
            try:
                session.commit()
                return "created"
            except IntegrityError as exc:
                session.rollback()
                return exc.orig.diag.constraint_name
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [access_world["one"], access_world["two"]]))
    assert sorted(results) == ["created", "uq_visitor_grant_active_" + scope]
    assert db.query(VisitorGrant).count() == 1


def test_scope_snapshots_do_not_follow_current_staff_owner_or_merge_businesses(db, access_world):
    a = VisitorGrant(**grant_data(access_world, "client"))
    b = VisitorGrant(**grant_data(access_world, "client", business_id=access_world["other"], account_id=access_world["two"]))
    db.add_all([a, b])
    db.commit()
    db.get(User, access_world["staff"]).business_id = access_world["other"]
    db.get(Client, access_world["client"]).owner_id = access_world["issuer"]
    db.commit()
    assert a.business_id == access_world["business"] and b.business_id == access_world["other"]
    assert a.account_id != b.account_id and a.client_id == b.client_id


def spent_invite(db, world):
    grant = VisitorGrant(**grant_data(world, "client"))
    db.add(grant)
    db.flush()
    receipt = VisitorClaim(**claim_data(world, consumed_at=NOW,
        consumed_account_id=world["one"], grant_id=grant.id))
    db.add(receipt)
    db.flush()
    result = grant.id, receipt.id
    db.commit()
    return result


@pytest.mark.parametrize("deleted", ["account", "grant"])
def test_spent_invite_survives_owner_or_grant_deletion_and_hash_cannot_be_reissued(db, access_world, deleted):
    grant_id, claim_id = spent_invite(db, access_world)
    if deleted == "account":
        db.execute(delete(VisitorAccount).where(VisitorAccount.id == access_world["one"]))
    else:
        db.execute(delete(VisitorGrant).where(VisitorGrant.id == grant_id))
    db.commit()
    receipt = db.get(VisitorClaim, claim_id)
    assert receipt.consumed_at == NOW and receipt.grant_id is None
    assert receipt.consumed_account_id == (None if deleted == "account" else access_world["one"])
    assert db.get(VisitorGrant, grant_id) is None
    assert rejected(db, VisitorClaim, claim_data(access_world)).constraint_name == "uq_visitor_claims_proof_hash"


def test_manage_token_cleanup_leaves_spent_receipt_and_existing_grant(db, access_world):
    grant = VisitorGrant(**grant_data(access_world))
    db.add(grant)
    db.flush()
    receipt = VisitorClaim(**claim_data(access_world, "manage_visit", grant_id=grant.id))
    db.add(receipt)
    db.flush()
    receipt_id, grant_id = receipt.id, grant.id
    db.commit()
    source = db.get(BookingManageToken, access_world["token"])
    assert source.revoked_at is None and source.expires_at == NOW + timedelta(days=7)
    db.execute(delete(BookingManageToken).where(BookingManageToken.id == source.id))
    db.commit()
    receipt = db.get(VisitorClaim, receipt_id)
    assert receipt.source_manage_token_id is None and receipt.consumed_at == NOW
    assert receipt.proof_hash == "c" * 64 and receipt.grant_id == grant_id
    assert db.get(VisitorGrant, grant_id).revoked_at is None
    assert rejected(db, VisitorClaim, claim_data(access_world, "manage_visit", source_manage_token_id=None)).constraint_name == "uq_visitor_claims_proof_hash"


def test_issuer_delete_can_null_fk_without_erasing_pending_invite_or_recipient(db, access_world):
    receipt = VisitorClaim(**claim_data(access_world))
    db.add(receipt)
    db.flush()
    receipt_id = receipt.id
    db.commit()
    db.execute(delete(User).where(User.id == access_world["issuer"]))
    db.commit()
    receipt = db.get(VisitorClaim, receipt_id)
    assert receipt.issued_by_user_id is None and receipt.consumed_at is None
    assert receipt.recipient_email == "recipient@example.com" and receipt.proof_hash == "a" * 64
    # Issuer authorization and whether this orphaned invite can be accepted belong to the future core.


@pytest.mark.parametrize("target", ["visit", "client", "business"])
def test_hard_deleted_target_cascades_only_its_grant_and_claim(db, access_world, target):
    values = grant_data(access_world, "visit" if target == "visit" else "client")
    proof_values = claim_data(access_world, "manage_visit" if target == "visit" else "client_invite")
    if target == "client":
        isolated = Client(full_name="Isolated target")
        db.add(isolated)
        db.flush()
        db.add(ClientBusinessRelation(client_id=isolated.id, business_id=access_world["business"]))
        values["client_id"] = proof_values["client_id"] = isolated.id
        model, target_id = Client, isolated.id
    elif target == "business":
        isolated = Business(name="Isolated business", business_type="salon")
        db.add(isolated)
        db.flush()
        db.add(ClientBusinessRelation(client_id=access_world["client"], business_id=isolated.id))
        values["business_id"] = proof_values["business_id"] = isolated.id
        model, target_id = Business, isolated.id
    else:
        model, target_id = Visit, access_world["visit"]
    grant = VisitorGrant(**values)
    db.add(grant)
    db.flush()
    proof_values["grant_id"] = grant.id
    proof_values["consumed_at"] = NOW
    proof_values["consumed_account_id"] = access_world["one"]
    receipt = VisitorClaim(**proof_values)
    db.add(receipt)
    db.flush()
    grant_id, receipt_id = grant.id, receipt.id
    db.commit()
    db.execute(delete(model).where(model.id == target_id))
    db.commit()
    assert db.get(VisitorGrant, grant_id) is None and db.get(VisitorClaim, receipt_id) is None
    assert db.get(VisitorAccount, access_world["one"]) is not None


@pytest.mark.parametrize("field", ["account_id", "business_id", "client_id", "visit_id"])
def test_grant_foreign_keys_do_not_accept_missing_targets(db, access_world, field):
    assert rejected(db, VisitorGrant, grant_data(access_world, **{field: 99999999})).constraint_name.endswith("_fkey")


@pytest.mark.parametrize("field", ["business_id", "client_id", "source_manage_token_id", "issued_by_user_id",
                                   "consumed_account_id", "grant_id"])
def test_claim_foreign_keys_do_not_accept_missing_targets(db, access_world, field):
    kind = "manage_visit" if field == "source_manage_token_id" else "client_invite"
    assert rejected(db, VisitorClaim, claim_data(access_world, kind, consumed_at=NOW, **{field: 99999999})).constraint_name.endswith("_fkey")
