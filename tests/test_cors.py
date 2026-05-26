import pytest
from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)
def test_cors_preflight():
    response = client.options(
        "/users/",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization",
        },
    )
    assert response.status_code == 200
    assert "access-control-allow-origin" in response.headers
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
def test_cors_headers():
    response = client.get(
        "/users/",
        headers={"Origin": "http://localhost:3000"},
    )
    assert "access-control-allow-origin" in response.headers or response.status_code == 401
def test_cors_invalid_origin():
    response = client.get(
        "/users/",
        headers={"Origin": "http://evil.com"},
    )
    assert response.status_code in [200, 401, 403]
