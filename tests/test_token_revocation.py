"""Access tokens are short-lived, and logging out everywhere kills the ones already handed out."""
from app.api.core import config

PASSWORD = "Str0ng!Pass-word"


def _login(client, user):
    r = client.post("/users/login", json={"email": user.email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()


def test_access_tokens_live_half_an_hour():
    assert config.settings.access_token_expire_minutes == 30


def test_logout_all_revokes_access_tokens_already_issued(client, make_user):
    user = make_user(role="specialist")
    phone = _login(client, user)["access_token"]
    laptop = _login(client, user)["access_token"]
    h_phone = {"Authorization": f"Bearer {phone}"}
    assert client.get("/users/me", headers=h_phone).status_code == 200

    r = client.post("/users/logout-all", headers={"Authorization": f"Bearer {laptop}"})
    assert r.status_code == 200

    assert client.get("/users/me", headers=h_phone).status_code == 401
    assert client.get("/users/me", headers={"Authorization": f"Bearer {laptop}"}).status_code == 401


def test_logging_in_again_right_after_logout_all_works(client, make_user):
    user = make_user(role="specialist")
    token = _login(client, user)["access_token"]
    client.post("/users/logout-all", headers={"Authorization": f"Bearer {token}"})
    fresh = _login(client, user)["access_token"]
    assert client.get("/users/me", headers={"Authorization": f"Bearer {fresh}"}).status_code == 200


def test_logout_all_keeps_submillisecond_precision():
    from datetime import datetime
    from types import SimpleNamespace
    from app.api.deps import token_revoked

    user = SimpleNamespace(tokens_valid_after=datetime(2030, 1, 1, 0, 0, 0, 900))
    issued_ms = 1893456000000
    assert token_revoked(user, {"iat_ms": issued_ms})
    assert token_revoked(user, {"iat_ms": issued_ms + 0.5})
    assert not token_revoked(user, {"iat_ms": issued_ms + 0.95})


def test_access_issue_time_preserves_submillisecond_precision(monkeypatch):
    import jwt
    from app.api.core import security

    monkeypatch.setattr(security.time, "time", lambda: 1893456000.0005)
    token = security.create_access_token({"sub": "1"})
    payload = jwt.decode(token, config.settings.jwt_secret,
                         algorithms=[config.settings.jwt_algorithm])
    assert payload["iat_ms"] == 1893456000000.5
