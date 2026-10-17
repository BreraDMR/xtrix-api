from starlette.requests import Request
from slowapi import Limiter
from app.api.core.config import settings
def client_ip(request: Request) -> str:
    """Who the request is from, for rate limiting.

    Behind a proxy we own (TRUST_PROXY=true, e.g. Render) the real client is the last address in
    X-Forwarded-For - the proxy appends it; everything left of it the client could have typed itself.
    Otherwise it's the socket peer, and X-Forwarded-For is ignored so it can't be spoofed.
    """
    if settings.trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "")
        last = forwarded.split(",")[-1].strip()
        if last:
            return last
    return request.client.host if request.client else "unknown"
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
    key_func=client_ip,
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
