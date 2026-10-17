from slowapi import Limiter
from slowapi.util import get_remote_address
from app.api.core.config import settings
storage_uri = "memory://"  
if settings.rate_limit_enabled and settings.redis_url.startswith("redis://"):
    try:
        import redis
        test_redis = redis.from_url(settings.redis_url, socket_connect_timeout=0.5, socket_timeout=0.5)
        test_redis.ping()
        storage_uri = settings.redis_url
        test_redis.close()
    except:
        storage_uri = "memory://"
limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=storage_uri,
)
def get_rate_limit_for_login():
    if not settings.rate_limit_enabled:
        return None
    return settings.rate_limit_login
def get_rate_limit_for_create():
    if not settings.rate_limit_enabled:
        return None
    return "10/minute"  
def get_rate_limit_for_read():
    if not settings.rate_limit_enabled:
        return None
    return "100/minute"  
