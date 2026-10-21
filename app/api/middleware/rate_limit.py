import time
from fastapi import Request
from fastapi.responses import JSONResponse
from limits import parse_many
from limits.storage import storage_from_string
from limits.strategies import FixedWindowRateLimiter
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware
from app.api.core.config import settings
from app.api.core.logger import logger


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


def user_or_ip(request: Request) -> str:
    """Per signed-in user for the assistant routes: a salon's staff share one address, but each has
    their own allowance. Only a valid access token counts; anything else falls back to the IP."""
    auth = request.headers.get("authorization", "")
    if auth[:7].lower() == "bearer ":
        try:
            import jwt
            payload = jwt.decode(auth[7:].strip(), settings.jwt_secret, algorithms=[settings.jwt_algorithm])
            if payload.get("type") == "access" and payload.get("sub"):
                return f"user:{payload['sub']}"
        except Exception:
            pass
    return client_ip(request)


def _storage_uri() -> str:
    # one counter store for every worker if Redis is there, else per-process memory
    if not (settings.rate_limit_enabled and settings.redis_url.startswith("redis://")):
        return "memory://"
    try:
        import redis
        probe = redis.from_url(settings.redis_url, socket_connect_timeout=0.5, socket_timeout=0.5)
        probe.ping()
        probe.close()
        logger.info("Redis connected, using it for rate limiting")
        return settings.redis_url
    except Exception as e:
        logger.warning(f"Redis unavailable ({str(e)[:50]}...), using in-memory rate limiting")
        return "memory://"


_STORAGE_URI = _storage_uri()

# The one limiter for per-route limits (@limiter.limit on login, signup, QR, exports...).
# (There used to be a second Limiter in main.py with the default limit, and it never ran.)
limiter = Limiter(
    key_func=client_ip,
    storage_uri=_STORAGE_URI,
    enabled=settings.rate_limit_enabled,
)

LOGIN_LIMIT = settings.rate_limit_login
SIGNUP_LIMIT = settings.rate_limit_signup
SENSITIVE_LIMIT = "10/minute"  # QR codes, 2FA, token refresh
EXPORT_LIMIT = "10/minute"
# per user (user_or_ip); the monthly allowance is the real cap, these only stop a runaway client
ASSISTANT_PLAN_LIMIT = "20/minute"
ASSISTANT_EXECUTE_LIMIT = "20/minute"
ASSISTANT_TRANSCRIBE_LIMIT = "10/minute"
# anonymous surface: catalog reads are cheap, slot scans and bookings are not
PUBLIC_BOOKING_READ_LIMIT = "60/minute"
PUBLIC_BOOKING_SLOTS_LIMIT = "30/minute"
PUBLIC_BOOKING_CREATE_LIMIT = "5/minute"
PUBLIC_BOOKING_MANAGE_LIMIT = "60/minute"
PUBLIC_BOOKING_MANAGE_ACTION_LIMIT = "10/minute"
SERVICE_PAYMENT_STATUS_LIMIT = "120/minute"
SERVICE_PAYMENT_ACTION_LIMIT = "10/minute"
GIFT_SHOP_READ_LIMIT = "60/minute"
GIFT_PURCHASE_LIMIT = "5/minute"
GIFT_ACTION_LIMIT = "10/minute"
PUBLIC_QUESTIONNAIRE_READ_LIMIT = "60/minute"
PUBLIC_QUESTIONNAIRE_SUBMIT_LIMIT = "10/minute"
# no account should be able to trigger reset mail faster than this; also bounds email guessing
PASSWORD_RESET_LIMIT = "5/minute"
PASSWORD_RESET_CONFIRM_LIMIT = "10/minute"
CLIENT_PORTAL_LOGIN_LIMIT = "5/minute"
CLIENT_PORTAL_VERIFY_LIMIT = "10/minute"
WAITLIST_JOIN_LIMIT = "5/minute"
WAITLIST_ACTION_LIMIT = "10/minute"


def _retry_after(exc: RateLimitExceeded) -> int:
    try:
        return int(exc.limit.limit.get_expiry())
    except Exception:
        return 60


def too_many_requests(retry: int) -> JSONResponse:
    # same shape as every other error, plus Retry-After (slowapi's own handler sent {"error": "..."})
    return JSONResponse(
        status_code=429,
        content={"error": {"message": f"Too many requests, try again in {retry} s",
                           "status_code": 429, "type": "RateLimitExceeded"}},
        headers={"Retry-After": str(retry)},
    )


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    logger.warning(f"Rate limit hit: {request.method} {request.url.path} - {exc.detail}")
    return too_many_requests(_retry_after(exc))


# health probes come every few seconds from the host, webhooks are checked by their own
# tokens/signatures and payment providers send them in bursts
DEFAULT_LIMIT_EXEMPT_PREFIXES = (
    "/metrics/health",
    "/subscriptions/webhooks/",
    "/public/booking/payments/webhooks/",
)


class DefaultRateLimitMiddleware(BaseHTTPMiddleware):
    """The default per-IP limit on every route.

    slowapi's own SlowAPIMiddleware can't find the endpoint behind FastAPI's included routers
    (it only sees the router object), so its default limit silently never applied. This counts
    by path prefix instead, in the same store as the per-route limits (Redis when there is one).
    """

    def __init__(self, app):
        super().__init__(app)
        self.items = parse_many(settings.rate_limit_default)
        self.strategy = FixedWindowRateLimiter(storage_from_string(_STORAGE_URI))

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if settings.rate_limit_enabled and not path.startswith(DEFAULT_LIMIT_EXEMPT_PREFIXES):
            ip = client_ip(request)
            for item in self.items:
                if not self.strategy.hit(item, "default", ip):
                    reset = self.strategy.get_window_stats(item, "default", ip).reset_time
                    logger.warning(f"Default rate limit hit: {request.method} {path}")
                    return too_many_requests(max(1, int(reset - time.time())))
        return await call_next(request)
