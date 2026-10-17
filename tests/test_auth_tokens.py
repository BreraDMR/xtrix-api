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


def test_docs_page_is_scalar(client):
    r = client.get("/docs")
    assert r.status_code == 200 and "@scalar/api-reference" in r.text
    assert client.get("/redoc").status_code == 404


def test_docs_in_production_sit_behind_basic_auth(client, monkeypatch):
    import base64

    monkeypatch.setattr(config.settings, "environment", "production")
    # not configured -> no docs at all
    monkeypatch.setattr(config.settings, "docs_username", "")
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404

    monkeypatch.setattr(config.settings, "docs_username", "alex")
    monkeypatch.setattr(config.settings, "docs_password", "s3cret-docs")
    r = client.get("/docs")
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic")
    bad = base64.b64encode(b"alex:nope").decode()
    assert client.get("/openapi.json", headers={"Authorization": f"Basic {bad}"}).status_code == 401
    good = {"Authorization": "Basic " + base64.b64encode(b"alex:s3cret-docs").decode()}
    assert client.get("/docs", headers=good).status_code == 200
    assert client.get("/openapi.json", headers=good).status_code == 200
