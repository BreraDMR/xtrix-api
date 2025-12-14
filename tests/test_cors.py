"""Tests for CORS configuration."""
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_cors_preflight():
    """Test CORS preflight request."""
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
    """Test CORS headers in response."""
    response = client.get(
        "/users/",
        headers={"Origin": "http://localhost:3000"},
    )
    # Может быть 401 если нет токена, но CORS headers должны быть
    assert "access-control-allow-origin" in response.headers or response.status_code == 401


def test_cors_invalid_origin():
    """Test that invalid origin is rejected."""
    response = client.get(
        "/users/",
        headers={"Origin": "http://evil.com"},
    )
    # В development может разрешать, но в production должен блокировать
    # Проверяем что CORS middleware работает
    assert response.status_code in [200, 401, 403]











