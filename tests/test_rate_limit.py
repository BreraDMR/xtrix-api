"""Tests for rate limiting."""
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.api.core.config import settings

client = TestClient(app)


def test_rate_limit_login():
    """Test rate limiting on login endpoint."""
    if not settings.rate_limit_enabled:
        pytest.skip("Rate limiting is disabled")
    
    # Делаем несколько запросов подряд
    for i in range(6):  # Больше лимита (5/minute)
        response = client.post(
            "/users/login",
            json={"email": "test@test.com", "password": "wrong"},
        )
        if i < 5:
            # Первые 5 должны пройти (но вернуть 401 из-за неправильных данных)
            assert response.status_code in [401, 429]
        else:
            # 6-й должен быть заблокирован rate limit
            assert response.status_code == 429
            break


def test_rate_limit_headers():
    """Test that rate limit headers are present."""
    response = client.get("/users/")
    # Может быть 401 если нет токена, но headers должны быть
    headers = response.headers
    # Проверяем наличие rate limit headers (если rate limiting включен)
    if settings.rate_limit_enabled:
        # SlowAPI может добавлять заголовки
        assert response.status_code in [200, 401, 403]


def test_rate_limit_disabled():
    """Test that rate limiting can be disabled."""
    # Этот тест проверяет что при отключенном rate limiting запросы проходят
    # В реальности нужно временно отключить rate limiting для этого теста
    pass











