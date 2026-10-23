"""Reset links stay private, expire, and can change one account only once."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest

from app.api.core.clock import utcnow
from app.api.core.security import verify_password
from app.api.crud import crud_password_reset
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.password_reset_token import PasswordResetToken
from app.db.models.user import User

NEW_PASSWORD = "Changed!Pass-word91"


@pytest.mark.parametrize("stored,requested", [
    ("reset.person@example.com", "Reset.Person@example.com"),
    ("Reset.Person@example.com", "reset.person@example.com"),
])
def test_reset_email_lookup_ignores_case_without_changing_the_account(client, db, make_user, stored, requested):
    owner = make_user(email=stored)
    response = client.post("/auth/password-reset/request", json={"email": requested})
    assert response.status_code == 200
    rows = db.query(NotificationOutbox).filter_by(kind="password_reset").all()
    assert len(rows) == 1
    assert rows[0].user_id == owner.id
    assert rows[0].recipient == stored
    assert db.get(User, owner.id).email == stored


def test_ambiguous_email_never_picks_an_account_for_reset(client, db, make_user):
    make_user(email="same.person@example.com")
    make_user(email="Same.Person@example.com")
    known = client.post("/auth/password-reset/request", json={"email": "same.person@example.com"})
    unknown = client.post("/auth/password-reset/request", json={"email": "missing@example.com"})
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    assert db.query(NotificationOutbox).filter_by(kind="password_reset").count() == 0


@pytest.mark.parametrize("state", ["expired", "used", "unknown", "deleted"])
def test_unusable_reset_link_changes_no_password(client, db, make_user, state):
    owner = make_user()
    row, raw = crud_password_reset.issue_token(db, owner.id)
    old_hash = owner.hashed_password
    if state == "expired":
        row.expires_at = utcnow() - timedelta(seconds=1)
    elif state == "used":
        row.used_at = utcnow()
    elif state == "deleted":
        owner.deleted_at = utcnow()
    else:
        raw = "not-the-issued-token" * 3
    db.commit()
    response = client.post("/auth/password-reset/confirm", json={"token": raw, "new_password": NEW_PASSWORD})
    assert response.status_code == 400
    db.refresh(owner)
    assert owner.hashed_password == old_hash


def test_reset_binds_to_token_owner_and_revokes_all_other_reset_links(client, db, make_user):
    owner, other = make_user(), make_user()
    stale, stale_raw = crud_password_reset.issue_token(db, owner.id)
    fresh, raw = crud_password_reset.issue_token(db, owner.id)
    other_hash = other.hashed_password
    assert db.query(PasswordResetToken).filter_by(token_hash=raw).count() == 0
    invalid = client.post("/auth/password-reset/confirm", json={"token": stale_raw, "new_password": NEW_PASSWORD})
    assert invalid.status_code == 400
    result = client.post("/auth/password-reset/confirm", json={"token": raw, "new_password": NEW_PASSWORD, "user_id": other.id})
    assert result.status_code == 200
    db.refresh(owner)
    db.refresh(other)
    assert verify_password(NEW_PASSWORD, owner.hashed_password)
    assert other.hashed_password == other_hash
    replay = client.post("/auth/password-reset/confirm", json={"token": raw, "new_password": "Another!Pass-word92"})
    assert replay.status_code == 400
    assert db.query(PasswordResetToken).filter_by(user_id=owner.id, used_at=None).count() == 0


def test_two_parallel_confirmations_consume_one_link_only_once(client, db, make_user):
    owner = make_user()
    _, raw = crud_password_reset.issue_token(db, owner.id)
    owner_id = owner.id
    db.rollback()
    gate = Barrier(2)
    passwords = [NEW_PASSWORD, "Another!Pass-word92"]

    def confirm(index):
        gate.wait(timeout=10)
        return client.post("/auth/password-reset/confirm", json={"token": raw, "new_password": passwords[index]})

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(confirm, range(2)))
    assert sorted(response.status_code for response in results) == [200, 400]
    winner = next(index for index, response in enumerate(results) if response.status_code == 200)
    db.expire_all()
    assert verify_password(passwords[winner], db.get(User, owner_id).hashed_password)
