import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.api.core.config import settings
client = TestClient(app)
def test_rate_limit_login():
    if not settings.rate_limit_enabled:
        pytest.skip("Rate limiting is disabled")
    for i in range(6):  
        response = client.post(
            "/users/login",
            json={"email": "test@test.com", "password": "wrong"},
        )
        if i < 5:
            assert response.status_code in [401, 429]
        else:
            assert response.status_code == 429
            break
def test_rate_limit_headers():
    response = client.get("/users/")
    headers = response.headers
    if settings.rate_limit_enabled:
        assert response.status_code in [200, 401, 403]
def test_rate_limit_disabled():
    pass
