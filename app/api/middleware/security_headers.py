from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from app.api.core.config import settings
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "geolocation=(), "
            "microphone=(), "
            "camera=(), "
            "payment=(), "
            "usb=(), "
            "magnetometer=(), "
            "gyroscope=(), "
            "accelerometer=()"
        )
        if settings.environment == "production":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; "  
                "includeSubDomains; "
                "preload"
            )
        is_docs_page = request.url.path in ["/docs", "/openapi.json"]
        csp = self._get_csp(is_docs=is_docs_page)
        if csp:
            response.headers["Content-Security-Policy"] = csp
        response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
        if "server" in response.headers:
            del response.headers["server"]
        return response
    def _get_csp(self, is_docs: bool = False) -> str | None:
        if not hasattr(settings, 'csp_policy') or not settings.csp_policy:
            if is_docs:
                return (
                    "default-src 'self'; "
                    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                    "img-src 'self' data: https:; "
                    "font-src 'self' data: https://cdn.jsdelivr.net https://fonts.scalar.com; "
                    "connect-src 'self'; "
                    "frame-ancestors 'none'; "
                    "base-uri 'self'; "
                    "form-action 'self'"
                )
            else:
                return (
                    "default-src 'self'; "
                    "script-src 'self'; "
                    "style-src 'self' 'unsafe-inline'; "
                    "img-src 'self' data: https:; "
                    "font-src 'self' data:; "
                    "connect-src 'self'; "
                    "frame-ancestors 'none'; "
                    "base-uri 'self'; "
                    "form-action 'self'"
                )
        return settings.csp_policy
