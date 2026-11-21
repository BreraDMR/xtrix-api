"""Accounts made through Google can add a password by email link, then change it in place."""
import re

import pytest
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter

from app.api.core import login_guard, password_reset_email
from app.api.core.security import verify_password
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.password_reset_token import PasswordResetToken
from app.db.models.refresh_token import RefreshToken
from app.db.models.user import User
from tests.test_google_login import google_setup, sign_in  # noqa: F401  (fixture used by name)

NEW_PASSWORD = "Fresh!Pass-word42"
OTHER_PASSWORD = "Second!Pass-word43"


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _google_owner(client, db, monkeypatch):
    response = sign_in(client, monkeypatch)
    assert response.status_code == 200, response.text
    return db.query(User).filter_by(email="invited@example.com").one(), response.json()


def _link_token(db, row):
    message = password_reset_email.render_password_reset(db, row)
    return message, re.search(r"#token=([^\s<\"]+)", message.text).group(1)


@pytest.mark.usefixtures("google_setup")
def test_me_tells_whether_the_account_has_a_password(client, db, make_user, headers_for, monkeypatch):
    _, tokens = _google_owner(client, db, monkeypatch)
    assert client.get("/users/me", headers=_bearer(tokens["access_token"])).json()["has_password"] is False
    regular = make_user()
    assert client.get("/users/me", headers=headers_for(regular)).json()["has_password"] is True


@pytest.mark.usefixtures("google_setup")
def test_google_account_sets_password_by_email_and_then_uses_both_logins(client, db, monkeypatch):
    user, tokens = _google_owner(client, db, monkeypatch)
    # nothing to type into the login form yet
    assert client.post("/users/login", json={"email": user.email, "password": ""}).status_code in (401, 422)

    response = client.post("/users/me/password/setup", headers=_bearer(tokens["access_token"]))
    assert response.status_code == 202, response.text
    row = db.query(NotificationOutbox).filter_by(kind="password_reset", user_id=user.id).one()
    assert row.recipient == user.email
    assert row.payload == {"purpose": "setup"}
    message, raw = _link_token(db, row)
    assert "Set a password" in message.subject

    confirm = client.post("/auth/password-reset/confirm", json={"token": raw, "new_password": NEW_PASSWORD})
    assert confirm.status_code == 200, confirm.text
    # the link is single use, like any reset link
    replay = client.post("/auth/password-reset/confirm", json={"token": raw, "new_password": OTHER_PASSWORD})
    assert replay.status_code == 400
    # and like a reset it ends the sessions that existed before
    assert client.get("/users/me", headers=_bearer(tokens["access_token"])).status_code == 401

    login = client.post("/users/login", json={"email": user.email, "password": NEW_PASSWORD})
    assert login.status_code == 200, login.text
    me = client.get("/users/me", headers=_bearer(login.json()["access_token"]))
    assert me.json()["has_password"] is True
    assert me.json()["id"] == user.id
    # typing the address with capitals still finds the lowercased Google account
    assert client.post("/users/login", json={"email": "Invited@Example.com", "password": NEW_PASSWORD}).status_code == 200
    # Google keeps working for the same account
    again = sign_in(client, monkeypatch)
    assert again.status_code == 200, again.text
    assert client.get("/users/me", headers=_bearer(again.json()["access_token"])).json()["id"] == user.id


def test_setup_link_is_refused_once_a_password_exists(client, db, make_user, headers_for):
    user = make_user()
    response = client.post("/users/me/password/setup", headers=headers_for(user))
    assert response.status_code == 409
    assert db.query(NotificationOutbox).count() == 0


def test_setup_needs_a_signed_in_user(client, db):
    assert client.post("/users/me/password/setup").status_code == 401
    assert db.query(NotificationOutbox).count() == 0


@pytest.mark.usefixtures("google_setup")
def test_setup_email_speaks_the_requested_language(client, db, monkeypatch):
    user, tokens = _google_owner(client, db, monkeypatch)
    headers = {**_bearer(tokens["access_token"]), "Accept-Language": "cz"}
    assert client.post("/users/me/password/setup", headers=headers).status_code == 202
    row = db.query(NotificationOutbox).filter_by(user_id=user.id).one()
    message, _ = _link_token(db, row)
    assert message.subject == "Nastavení hesla pro Xtrix"


def test_plain_reset_email_keeps_its_own_wording(client, db, make_user):
    user = make_user(email="plain.reset@example.com")
    assert client.post("/auth/password-reset/request", json={"email": user.email}).status_code == 200
    row = db.query(NotificationOutbox).filter_by(user_id=user.id).one()
    message, _ = _link_token(db, row)
    assert message.subject == "Reset your Xtrix password"


def test_change_password_needs_the_current_one(client, db, make_user, headers_for):
    user = make_user()
    old_hash = user.hashed_password
    wrong = client.post("/users/me/password", headers=headers_for(user),
                        json={"current_password": "Wrong!Pass-word1", "new_password": NEW_PASSWORD})
    assert wrong.status_code == 400
    weak = client.post("/users/me/password", headers=headers_for(user),
                       json={"current_password": "Str0ng!Pass-word", "new_password": "short"})
    assert weak.status_code == 422
    db.refresh(user)
    assert user.hashed_password == old_hash


def test_change_password_keeps_this_device_and_signs_out_the_rest(client, db, make_user):
    user = make_user()
    first = client.post("/users/login", json={"email": user.email, "password": "Str0ng!Pass-word"}).json()
    second = client.post("/users/login", json={"email": user.email, "password": "Str0ng!Pass-word"}).json()
    stale_link = client.post("/auth/password-reset/request", json={"email": user.email})
    assert stale_link.status_code == 200
    _, raw = _link_token(db, db.query(NotificationOutbox).filter_by(user_id=user.id).one())

    response = client.post("/users/me/password", headers=_bearer(first["access_token"]),
                           json={"current_password": "Str0ng!Pass-word", "new_password": NEW_PASSWORD})
    assert response.status_code == 200, response.text
    fresh = response.json()
    db.expire_all()
    assert verify_password(NEW_PASSWORD, db.get(User, user.id).hashed_password)
    # the old access tokens and refresh tokens of every device stop working
    assert client.get("/users/me", headers=_bearer(second["access_token"])).status_code == 401
    assert client.get("/users/me", headers=_bearer(first["access_token"])).status_code == 401
    assert client.post("/users/refresh", json={"refresh_token": second["refresh_token"]}).status_code == 401
    # the pair handed back keeps this device signed in
    assert client.get("/users/me", headers=_bearer(fresh["access_token"])).status_code == 200
    assert client.post("/users/refresh", json={"refresh_token": fresh["refresh_token"]}).status_code == 200
    assert db.query(RefreshToken).filter_by(user_id=user.id, is_revoked=False).count() == 1
    # a reset link sent before the change can't undo it
    assert db.query(PasswordResetToken).filter_by(user_id=user.id, used_at=None).count() == 0
    assert client.post("/auth/password-reset/confirm",
                       json={"token": raw, "new_password": OTHER_PASSWORD}).status_code == 400
    assert client.post("/users/login", json={"email": user.email, "password": NEW_PASSWORD}).status_code == 200


@pytest.mark.usefixtures("google_setup")
def test_change_password_is_refused_without_a_password_to_check(client, db, monkeypatch):
    user, tokens = _google_owner(client, db, monkeypatch)
    response = client.post("/users/me/password", headers=_bearer(tokens["access_token"]),
                           json={"current_password": "anything", "new_password": NEW_PASSWORD})
    assert response.status_code == 409
    db.refresh(user)
    assert user.hashed_password == ""


@pytest.fixture
def own_guard(monkeypatch):
    # a private counter store, so failures here don't lock user 1 in the next test
    storage = MemoryStorage()
    monkeypatch.setattr(login_guard, "_strategy", MovingWindowRateLimiter(storage))
    yield
    if storage.timer:
        storage.timer.cancel()
    storage.reset()


def test_wrong_current_password_is_throttled_like_login(client, db, make_user, headers_for, own_guard):
    user = make_user()
    statuses = [
        client.post("/users/me/password", headers=headers_for(user),
                    json={"current_password": f"Wrong!Pass-word{n}", "new_password": NEW_PASSWORD}).status_code
        for n in range(12)
    ]
    assert 429 in statuses
    # once throttled, even the right password waits
    right = client.post("/users/me/password", headers=headers_for(user),
                        json={"current_password": "Str0ng!Pass-word", "new_password": NEW_PASSWORD})
    assert right.status_code == 429
