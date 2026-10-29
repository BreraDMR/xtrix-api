"""Run against the real migrated throwaway Postgres, never create_all or SQLite."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.core.clock import utcnow
from app.db.models.visitor_account import GOOGLE_ISSUER, VisitorAccount, VisitorGoogleIdentity


def account(db, email="visitor@example.com", **changes):
    row = VisitorAccount(email_canonical=email, **{"email_verified_at": utcnow(), **changes})
    db.add(row)
    db.commit()
    return row


@pytest.mark.parametrize("first,second", [
    ("  Guest@Example.COM  ", "guest@example.com"),
    ("Straße@example.com", "STRASSE@example.com"),
    ("\u2003Guest@example.com\u2003", "guest@example.com"),
])
def test_casefolded_email_key_is_unique_in_the_database(db, first, second):
    original = account(db, first)
    first_id = original.id
    with pytest.raises(IntegrityError):
        account(db, second)
    db.rollback()
    assert db.query(VisitorAccount).count() == 1
    assert db.get(VisitorAccount, first_id).email_canonical == first.strip().casefold()


def test_plus_and_dots_are_not_removed_from_email_identity(db):
    ids = {account(db, email).id for email in (
        "guest.name@gmail.com", "guestname@gmail.com", "guest.name+family@gmail.com")}
    assert len(ids) == 3


def test_a_verified_google_only_account_does_not_require_a_password(db):
    row = account(db)
    db.add(VisitorGoogleIdentity(account_id=row.id, issuer=GOOGLE_ISSUER, subject="google-subject"))
    db.commit()
    assert db.get(VisitorAccount, row.id).hashed_password is None
    assert db.query(VisitorGoogleIdentity).filter_by(account_id=row.id).count() == 1


@pytest.mark.parametrize("password", [None, "a-previously-hashed-password"])
def test_database_requires_email_proof_before_any_account_exists(db, password):
    with pytest.raises(IntegrityError):
        account(db, email_verified_at=None, hashed_password=password)
    db.rollback()
    assert db.query(VisitorAccount).count() == 0


def test_empty_password_is_not_a_google_only_password_marker(db):
    with pytest.raises(IntegrityError):
        account(db, hashed_password="")
    db.rollback()
    assert db.query(VisitorAccount).count() == 0


def test_google_principal_cannot_be_linked_to_a_second_account(db):
    first = account(db, "first@example.com")
    second = account(db, "second@example.com")
    first_id, second_id = first.id, second.id
    db.execute(insert(VisitorGoogleIdentity).values(issuer=GOOGLE_ISSUER, subject="stable-sub", account_id=first_id))
    db.commit()
    with pytest.raises(IntegrityError):
        db.execute(insert(VisitorGoogleIdentity).values(issuer=GOOGLE_ISSUER, subject="stable-sub", account_id=second_id))
        db.commit()
    db.rollback()
    assert db.get(VisitorGoogleIdentity, (GOOGLE_ISSUER, "stable-sub")).account_id == first_id


@pytest.mark.parametrize("identity", [
    {"issuer": "https://attacker.example", "subject": "sub"},
    {"issuer": GOOGLE_ISSUER, "subject": ""},
    {"issuer": GOOGLE_ISSUER, "subject": " sub "},
])
def test_database_rejects_invalid_google_principals(db, identity):
    owner_id = account(db).id
    with pytest.raises(IntegrityError):
        db.execute(insert(VisitorGoogleIdentity).values(account_id=owner_id, **identity))
        db.commit()
    db.rollback()
    assert db.query(VisitorGoogleIdentity).count() == 0


def test_google_identity_needs_an_existing_visitor_account(db):
    with pytest.raises(IntegrityError):
        db.execute(insert(VisitorGoogleIdentity).values(issuer=GOOGLE_ISSUER, subject="sub", account_id=99999))
        db.commit()
    db.rollback()


def test_identity_conflict_rolls_back_the_new_account_too(db):
    first_id = account(db, "first@example.com").id
    db.execute(insert(VisitorGoogleIdentity).values(issuer=GOOGLE_ISSUER, subject="stable-sub", account_id=first_id))
    db.commit()
    with pytest.raises(IntegrityError):
        second = VisitorAccount(email_canonical="second@example.com", email_verified_at=utcnow())
        db.add(second)
        db.flush()
        db.execute(insert(VisitorGoogleIdentity).values(issuer=GOOGLE_ISSUER, subject="stable-sub", account_id=second.id))
        db.commit()
    db.rollback()
    assert db.query(VisitorAccount).count() == 1
    assert db.get(VisitorGoogleIdentity, (GOOGLE_ISSUER, "stable-sub")).account_id == first_id


def test_database_cascades_only_visitor_identities_on_account_deletion(db):
    owner_id = account(db).id
    db.execute(insert(VisitorGoogleIdentity).values(issuer=GOOGLE_ISSUER, subject="sub", account_id=owner_id))
    db.commit()
    db.execute(text("DELETE FROM visitor_accounts WHERE id=:id"), {"id": owner_id})
    db.commit()
    assert db.query(VisitorGoogleIdentity).count() == 0


def test_two_concurrent_normalized_signups_cannot_create_two_accounts(db):
    bind = db.get_bind()
    barrier = Barrier(2)
    def create(email):
        with Session(bind) as session:
            row = VisitorAccount(email_canonical=email, email_verified_at=utcnow())
            session.add(row)
            barrier.wait(timeout=5)
            try:
                session.commit()
                return "created"
            except IntegrityError:
                session.rollback()
                return "duplicate"
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(create, [" RACE@example.com ", "race@EXAMPLE.COM"]))
    assert sorted(outcomes) == ["created", "duplicate"]
    assert db.execute(select(VisitorAccount.email_canonical)).scalars().all() == ["race@example.com"]
