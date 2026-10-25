"""Credential boundaries are database invariants, not only endpoint assumptions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier

import pytest
from sqlalchemy import insert, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.visitor_account import VisitorAccount
from app.db.models.visitor_auth import VisitorChallenge, VisitorSession

NOW = datetime(2026, 10, 25)


def account(db, email="visitor@example.com"):
    row = VisitorAccount(email_canonical=email, email_verified_at=NOW)
    db.add(row)
    db.commit()
    return row.id


def payload(purpose="register", account_id=None, **changes):
    google = purpose in {"google_login", "google_link"}
    return {"purpose": purpose, "token_hash": "a" * 64, "account_id": account_id,
            "email_canonical": None if google else "visitor@example.com",
            "origin": "https://xtrix.example" if google else None,
            "created_at": NOW, "expires_at": NOW + timedelta(minutes=30), **changes}


@pytest.mark.parametrize("purpose", ["register", "reset", "email_change", "google_login", "google_link"])
def test_each_purpose_has_one_explicit_target_shape(db, purpose):
    owner = None if purpose in {"register", "google_login"} else account(db)
    db.execute(insert(VisitorChallenge).values(**payload(purpose, owner)))
    db.commit()
    row = db.query(VisitorChallenge).one()
    assert row.purpose == purpose and row.account_id == owner and row.consumed_at is None


@pytest.mark.parametrize("purpose,changes", [
    ("register", {"account_id": 99999}), ("register", {"email_canonical": None}),
    ("register", {"origin": "https://xtrix.example"}),
    ("reset", {"account_id": None}), ("reset", {"email_canonical": None}),
    ("email_change", {"account_id": None}), ("email_change", {"email_canonical": None}),
    ("email_change", {"origin": "https://xtrix.example"}),
    ("google_login", {"account_id": 99999}), ("google_login", {"email_canonical": "claimed@example.com"}),
    ("google_login", {"origin": None}), ("google_login", {"origin": ""}),
    ("google_link", {"account_id": None}), ("google_link", {"email_canonical": "claimed@example.com"}),
    ("google_link", {"origin": None}), ("google_link", {"origin": "  "}),
    ("google_login", {"origin": "\t"}), ("google_login", {"origin": "https://xtrix.example\n"}),
    ("reset", {"origin": "https://xtrix.example"}),
])
def test_database_rejects_cross_purpose_target_shapes(db, purpose, changes):
    owner = None if purpose in {"register", "google_login"} else account(db)
    data = payload(purpose, owner)
    # Supply a real FK when testing forbidden account binding, so the scope CHECK is the guard.
    if changes.get("account_id") == 99999:
        changes = {**changes, "account_id": account(db, "bound@example.com")}
    data.update(changes)
    with pytest.raises(IntegrityError):
        db.execute(insert(VisitorChallenge).values(**data))
        db.commit()
    db.rollback()
    assert db.query(VisitorChallenge).count() == 0


@pytest.mark.parametrize("changes", [
    {"purpose": "crm_login"}, {"purpose": ""}, {"purpose": None},
    {"token_hash": ""}, {"token_hash": "a" * 63}, {"token_hash": "A" * 64},
    {"token_hash": "g" * 64}, {"token_hash": None},
    {"expires_at": NOW}, {"expires_at": NOW - timedelta(seconds=1)},
    {"email_canonical": ""}, {"email_canonical": " visitor@example.com "},
])
def test_database_rejects_invalid_challenge_material(db, changes):
    with pytest.raises(IntegrityError):
        db.execute(insert(VisitorChallenge).values(**payload(**changes)))
        db.commit()
    db.rollback()


def test_email_challenges_share_the_account_casefold_rule(db):
    row = VisitorChallenge(**payload(email_canonical="  Straße@EXAMPLE.COM  "))
    db.add(row)
    db.commit()
    assert db.query(VisitorChallenge).one().email_canonical == "strasse@example.com"


def test_the_same_digest_cannot_move_between_challenge_purposes(db):
    db.execute(insert(VisitorChallenge).values(**payload()))
    db.commit()
    with pytest.raises(IntegrityError):
        db.execute(insert(VisitorChallenge).values(**payload("google_login")))
        db.commit()
    db.rollback()
    assert db.query(VisitorChallenge).one().purpose == "register"


@pytest.mark.parametrize("table", [VisitorChallenge, VisitorSession])
def test_database_rejects_a_missing_visitor_fk(db, table):
    data = payload("reset", 99999) if table is VisitorChallenge else {
        "account_id": 99999, "token_hash": "b" * 64, "created_at": NOW,
        "expires_at": NOW + timedelta(days=30)}
    with pytest.raises(IntegrityError):
        db.execute(insert(table).values(**data))
        db.commit()
    db.rollback()


@pytest.mark.parametrize("changes", [
    {"account_id": None}, {"token_hash": ""}, {"token_hash": "A" * 64},
    {"token_hash": "a" * 63}, {"token_hash": None},
    {"expires_at": NOW}, {"expires_at": NOW - timedelta(seconds=1)},
])
def test_database_rejects_invalid_session_material(db, changes):
    data = {"account_id": account(db), "token_hash": "b" * 64, "created_at": NOW,
            "expires_at": NOW + timedelta(days=30), **changes}
    with pytest.raises(IntegrityError):
        db.execute(insert(VisitorSession).values(**data))
        db.commit()
    db.rollback()


def test_revocation_and_consumption_are_durable_without_raw_secrets(db):
    owner = account(db)
    challenge = VisitorChallenge(**payload("reset", owner))
    session = VisitorSession(account_id=owner, token_hash="b" * 64, created_at=NOW,
                             expires_at=NOW + timedelta(days=30))
    db.add_all([challenge, session])
    db.commit()
    challenge.consumed_at = session.revoked_at = NOW + timedelta(minutes=1)
    db.commit()
    db.expire_all()
    assert db.query(VisitorChallenge).one().consumed_at == NOW + timedelta(minutes=1)
    assert db.query(VisitorSession).one().revoked_at == NOW + timedelta(minutes=1)
    for table in (VisitorChallenge.__table__, VisitorSession.__table__):
        assert {"token", "raw_token", "password", "credential", "nonce", "secret"}.isdisjoint(table.columns.keys())


def test_account_delete_cascades_only_its_bound_credentials(db):
    first, second = account(db), account(db, "second@example.com")
    for i, owner in enumerate([first, second], start=1):
        db.execute(insert(VisitorSession).values(account_id=owner, token_hash=str(i) * 64,
            created_at=NOW, expires_at=NOW + timedelta(days=30)))
        db.execute(insert(VisitorChallenge).values(**payload("reset", owner, token_hash=str(i + 2) * 64)))
    db.execute(insert(VisitorChallenge).values(**payload(token_hash="f" * 64)))
    db.commit()
    db.execute(text("DELETE FROM visitor_accounts WHERE id=:id"), {"id": first})
    db.commit()
    assert [row.account_id for row in db.query(VisitorSession).all()] == [second]
    assert {row.account_id for row in db.query(VisitorChallenge).all()} == {None, second}


@pytest.mark.parametrize("table", [VisitorChallenge, VisitorSession])
def test_concurrent_equal_token_digests_have_only_one_winner(db, table):
    owner = account(db)
    bind = db.get_bind()
    barrier = Barrier(2)
    def create(_):
        with Session(bind) as session:
            row = (VisitorChallenge(**payload("reset", owner)) if table is VisitorChallenge else
                VisitorSession(account_id=owner, token_hash="b" * 64, created_at=NOW,
                               expires_at=NOW + timedelta(days=30)))
            session.add(row)
            barrier.wait(timeout=5)
            try:
                session.commit()
                return "created"
            except IntegrityError:
                session.rollback()
                return "duplicate"
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(create, range(2)))
    assert sorted(outcomes) == ["created", "duplicate"]
    assert db.query(table).count() == 1
