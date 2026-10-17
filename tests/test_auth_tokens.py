from datetime import timedelta

import jwt

from app.api.core import config
from app.api.core.security import create_access_token, create_refresh_token


def test_expired_access_token_is_rejected(client, test_user):
    token = create_access_token({"sub": str(test_user.id)}, expires_delta=timedelta(seconds=-5))
    assert client.get("/users/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_token_signed_with_another_key_is_rejected(client, test_user):
    forged = jwt.encode({"sub": str(test_user.id), "type": "access"}, "x" * 40, algorithm="HS256")
    assert client.get("/users/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_refresh_token_does_not_work_as_access_token(client, test_user):
    refresh, _ = create_refresh_token({"sub": str(test_user.id)})
    assert client.get("/users/me", headers={"Authorization": f"Bearer {refresh}"}).status_code == 401


def test_valid_access_token_works(client, test_user, auth_headers):
    assert client.get("/users/me", headers=auth_headers).status_code == 200


def test_docs_in_production_need_a_live_access_token(client, test_user, auth_headers, monkeypatch):
    monkeypatch.setattr(config.settings, "environment", "production")
    refresh, _ = create_refresh_token({"sub": str(test_user.id)})
    assert client.get("/openapi.json").status_code == 401
    assert client.get("/openapi.json", headers={"Authorization": f"Bearer {refresh}"}).status_code == 401
    assert client.get("/openapi.json", headers=auth_headers).status_code == 200
