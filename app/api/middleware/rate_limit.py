"""Rate limiting middleware for specific endpoints."""
from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from app.api.core.config import settings

# Создаем limiter для эндпоинтов
# Используем память по умолчанию, Redis только если доступен
storage_uri = "memory://"  # По умолчанию используем память

if settings.rate_limit_enabled and settings.redis_url.startswith("redis://"):
    try:
        import redis
        # Проверяем доступность Redis
        test_redis = redis.from_url(settings.redis_url, socket_connect_timeout=0.5, socket_timeout=0.5)
        test_redis.ping()
        storage_uri = settings.redis_url
        test_redis.close()
    except:
        # Если Redis недоступен, используем память
        storage_uri = "memory://"

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=storage_uri,
)


def get_rate_limit_for_login():
    """Get rate limit for login endpoint."""
    if not settings.rate_limit_enabled:
        return None
    return settings.rate_limit_login


def get_rate_limit_for_create():
    """Get rate limit for create endpoints."""
    if not settings.rate_limit_enabled:
        return None
    return "10/minute"  # 10 созданий в минуту


def get_rate_limit_for_read():
    """Get rate limit for read endpoints."""
    if not settings.rate_limit_enabled:
        return None
    return "100/minute"  # 100 чтений в минуту

