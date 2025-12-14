"""Security headers middleware."""
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from app.api.core.config import settings


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Middleware для добавления security headers к каждому ответу."""
    
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        
        # X-Content-Type-Options: предотвращает MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"
        
        # X-Frame-Options: предотвращает clickjacking
        response.headers["X-Frame-Options"] = "DENY"
        
        # X-XSS-Protection: защита от XSS (для старых браузеров)
        response.headers["X-XSS-Protection"] = "1; mode=block"
        
        # Referrer-Policy: контроль передачи referrer
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        
        # Permissions-Policy: контроль доступа к API браузера
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
        
        # Strict-Transport-Security (HSTS): только для HTTPS
        if settings.environment == "production":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; "  # 1 год
                "includeSubDomains; "
                "preload"
            )
        
        # Content-Security-Policy: настраивается через конфигурацию
        # Для страниц документации разрешаем загрузку из CDN
        is_docs_page = request.url.path in ["/docs", "/redoc", "/openapi.json"]
        csp = self._get_csp(is_docs=is_docs_page)
        if csp:
            response.headers["Content-Security-Policy"] = csp
        
        # X-Permitted-Cross-Domain-Policies: для Flash/PDF
        response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
        
        # Remove server header (если возможно)
        if "server" in response.headers:
            del response.headers["server"]
        
        return response
    
    def _get_csp(self, is_docs: bool = False) -> str | None:
        """Получить Content-Security-Policy из настроек."""
        if not hasattr(settings, 'csp_policy') or not settings.csp_policy:
            if is_docs:
                # Для страниц документации разрешаем загрузку из CDN для Swagger UI
                return (
                    "default-src 'self'; "
                    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                    "img-src 'self' data: https:; "
                    "font-src 'self' data: https://cdn.jsdelivr.net; "
                    "connect-src 'self'; "
                    "frame-ancestors 'none'; "
                    "base-uri 'self'; "
                    "form-action 'self'"
                )
            else:
                # Базовый CSP для API (разрешаем только self)
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




