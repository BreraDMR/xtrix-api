from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from sqlalchemy.exc import SQLAlchemyError
import time

from app.api.core.config import settings
from app.api.core.logger import logger
from app.api.middleware.security_headers import SecurityHeadersMiddleware
from app.api.v1.endpoints import (
    clients, metrics, services, users, visits, files, subscriptions, schedules, 
    analytics, audit, archiving, specialist_profiles
)
from app.api.core import error_handlers

app = FastAPI(
    title="Xtrix API",
    description="""
    ## Xtrix API Documentation
    
    Comprehensive API for managing beauty salon operations including clients, visits, services, and analytics.
    
    ### Features
    - 🔐 JWT-based authentication with refresh tokens
    - 👥 Role-based access control (admin, specialist, client)
    - 📊 Advanced analytics and reporting
    - 📱 Mobile app support with offline mode
    - 🔔 Push notifications
    - 📈 Real-time monitoring and metrics
    - 🌍 Multi-language support (EN, RU, CZ)
    - 🔒 Security features (rate limiting, CORS, audit logs)
    
    ### Authentication
    Most endpoints require authentication via JWT token. Include the token in the Authorization header:
    ```
    Authorization: Bearer <your_access_token>
    ```
    
    ### Rate Limiting
    API requests are rate-limited to prevent abuse. Default limits:
    - General endpoints: 1000 requests/hour
    - Login endpoint: 5 requests/minute
    
    ### Pagination
    List endpoints support pagination with `page` and `per_page` parameters.
    
    ### Localization
    Set `Accept-Language` header to get localized responses:
    - `en` - English
    - `ru` - Russian
    - `cz` - Czech
    
    ### Error Handling
    All errors return standardized JSON responses with detailed messages in development mode.
    """,
    version="1.0.0",
    terms_of_service="https://example.com/terms/",
    contact={
        "name": "API Support",
        "email": "support@xtrix.com",
    },
    license_info={
        "name": "MIT",
    },
    servers=[
        {
            "url": "http://localhost:8000",
            "description": "Development server"
        },
        {
            "url": "https://api.xtrix.com",
            "description": "Production server"
        },
    ],
    redirect_slashes=False,  # Disable automatic redirects for trailing slashes
)

# Register exception handlers for detailed error messages
app.add_exception_handler(RequestValidationError, error_handlers.validation_exception_handler)
app.add_exception_handler(SQLAlchemyError, error_handlers.database_exception_handler)
app.add_exception_handler(HTTPException, error_handlers.http_exception_handler)
app.add_exception_handler(Exception, error_handlers.general_exception_handler)

# Security Headers Middleware (добавляется первым)
app.add_middleware(SecurityHeadersMiddleware)

# Middleware для логирования запросов и метрик
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all HTTP requests and collect metrics."""
    start_time = time.time()
    
    # Логируем входящий запрос
    logger.info(
        f"Request: {request.method} {request.url.path} - "
        f"Client: {request.client.host if request.client else 'unknown'}"
    )
    
    try:
        response = await call_next(request)
        process_time = time.time() - start_time
        
        # Collect metrics
        from app.api.core.monitoring import metrics_collector
        metrics_collector.record_request(
            request.method,
            request.url.path,
            response.status_code,
            process_time
        )
        
        # Логируем ответ
        logger.info(
            f"Response: {request.method} {request.url.path} - "
            f"Status: {response.status_code} - "
            f"Time: {process_time:.3f}s"
        )
        
        return response
    except Exception as e:
        process_time = time.time() - start_time
        
        # Record error metric
        from app.api.core.monitoring import metrics_collector
        metrics_collector.record_request(
            request.method,
            request.url.path,
            500,
            process_time
        )
        
        logger.error(
            f"Error: {request.method} {request.url.path} - "
            f"Exception: {str(e)} - "
            f"Time: {process_time:.3f}s",
            exc_info=True,
        )
        raise

# Rate Limiting Configuration
if settings.rate_limit_enabled:
    # Используем память вместо Redis для разработки (если Redis недоступен)
    # В production используйте Redis для лучшей производительности
    storage_uri = "memory://"  # По умолчанию используем память
    
    # Пытаемся использовать Redis только если он явно настроен и доступен
    if settings.redis_url.startswith("redis://"):
        try:
            import redis
            # Проверяем доступность Redis с коротким таймаутом
            test_redis = redis.from_url(settings.redis_url, socket_connect_timeout=0.5, socket_timeout=0.5)
            test_redis.ping()
            storage_uri = settings.redis_url
            test_redis.close()
            logger.info("Redis подключен, используется для rate limiting")
        except Exception as e:
            # Если Redis недоступен, используем память
            logger.warning(f"Redis недоступен ({str(e)[:50]}...), используется memory storage для rate limiting")
            storage_uri = "memory://"
    else:
        logger.info("Используется memory storage для rate limiting (Redis URL не настроен)")
    
    app_limiter = Limiter(
        key_func=get_remote_address,
        default_limits=[settings.rate_limit_default],
        storage_uri=storage_uri,
    )
    app.state.limiter = app_limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS Configuration
# В продакшене настройте через переменную окружения CORS_ORIGINS
def get_cors_origins():
    """Get CORS origins from settings or environment variable."""
    # Базовые локальные адреса для разработки
    dev_origins = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ]
    
    # Дополнительные из .env
    env_origins = settings.get_cors_origins_list()
    
    if settings.environment == "production":
        # В продакшене используем только указанные домены из .env
        return env_origins if env_origins else dev_origins
    else:
        # В разработке разрешаем локальные + из .env
        return list(set(dev_origins + env_origins))  # Убираем дубликаты

# Настройка CORS
if settings.environment == "production":
    # В продакшене используем только указанные origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_cors_origins(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Accept",
            "Accept-Language",
            "X-Requested-With",
        ],
        expose_headers=["X-Total-Count", "X-Page", "X-Per-Page"],
        max_age=3600,  # 1 hour
    )
else:
    # В разработке разрешаем все localhost порты (для Flutter Web и других инструментов)
    import re
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",  # Разрешаем любой localhost порт
        allow_origins=get_cors_origins(),  # Плюс явно указанные
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Accept",
            "Accept-Language",
            "X-Requested-With",
        ],
        expose_headers=["X-Total-Count", "X-Page", "X-Per-Page"],
        max_age=3600,  # 1 hour
    )

# Root endpoint
@app.get("/")
async def root():
    """Root endpoint - API information."""
    return {
        "name": "Xtrix API",
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs",
        "health": "/metrics/health"
    }

app.include_router(users.router, prefix="/users", tags=["users"])
app.include_router(clients.router, prefix="/clients", tags=["clients"])
app.include_router(services.router, prefix="/services", tags=["services"])
app.include_router(visits.router, prefix="/visits", tags=["visits"])
app.include_router(metrics.router, prefix="/metrics", tags=["metrics"])
app.include_router(files.router, prefix="/files", tags=["files"])
app.include_router(subscriptions.router, prefix="/subscriptions", tags=["subscriptions"])
app.include_router(schedules.router, prefix="/schedules", tags=["schedules"])
app.include_router(analytics.router, prefix="/analytics", tags=["analytics"])
app.include_router(audit.router, prefix="/audit", tags=["audit"])
app.include_router(archiving.router, prefix="/archiving", tags=["archiving"])
app.include_router(specialist_profiles.router, prefix="/specialist-profiles", tags=["specialist-profiles"])
