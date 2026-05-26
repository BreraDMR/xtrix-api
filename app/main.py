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
    analytics, audit, archiving, specialist_profiles, client_relations, employees, businesses, products, expenses, loyalty, user_preferences
)
from app.api.core import error_handlers
app = FastAPI(
    title="Xtrix API",
    version="1.0.0",
    redirect_slashes=True,
)
app.add_exception_handler(RequestValidationError, error_handlers.validation_exception_handler)
app.add_exception_handler(SQLAlchemyError, error_handlers.database_exception_handler)
app.add_exception_handler(HTTPException, error_handlers.http_exception_handler)
app.add_exception_handler(Exception, error_handlers.general_exception_handler)
@app.middleware("http")
async def protect_docs(request: Request, call_next):
    if request.url.path in ["/docs", "/redoc", "/openapi.json"]:
        if settings.environment == "production":
            from app.api.deps import get_current_user
            from fastapi import Depends
            from fastapi.security import HTTPBearer
            from sqlalchemy.orm import Session
            from app.db.session import SessionLocal
            auth_header = request.headers.get("Authorization")
            if not auth_header or not auth_header.startswith("Bearer "):
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    status_code=401,
                    content={
                        "detail": "Authentication required to access API documentation. Please provide a valid JWT token in the Authorization header: Authorization: Bearer <your_token>"
                    }
                )
            token = auth_header.replace("Bearer ", "")
            try:
                from jose import JWTError, jwt
                payload = jwt.decode(
                    token, 
                    settings.jwt_secret, 
                    algorithms=[settings.jwt_algorithm]
                )
                user_id: int = payload.get("sub")
                if user_id is None:
                    raise HTTPException(status_code=401, detail="Invalid token")
                db = SessionLocal()
                try:
                    from app.api.crud import crud_user
                    user = crud_user.get_user(db, user_id)
                    if not user or (hasattr(user, 'is_active') and not user.is_active):
                        raise HTTPException(status_code=401, detail="User not found or inactive")
                finally:
                    db.close()
            except (JWTError, HTTPException) as e:
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    status_code=401,
                    content={
                        "detail": f"Invalid or expired token: {str(e)}"
                    }
                )
    response = await call_next(request)
    return response
@app.middleware("http")
async def fix_proxy_headers(request: Request, call_next):
    if request.headers.get("X-Forwarded-Proto") == "https":
        request.scope["scheme"] = "https"
    elif request.headers.get("X-Forwarded-Ssl") == "on":
        request.scope["scheme"] = "https"
    response = await call_next(request)
    return response
app.add_middleware(SecurityHeadersMiddleware)
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    logger.info(
        f"Request: {request.method} {request.url.path} - "
        f"Client: {request.client.host if request.client else 'unknown'}"
    )
    try:
        response = await call_next(request)
        process_time = time.time() - start_time
        from app.api.core.monitoring import metrics_collector
        metrics_collector.record_request(
            request.method,
            request.url.path,
            response.status_code,
            process_time
        )
        logger.info(
            f"Response: {request.method} {request.url.path} - "
            f"Status: {response.status_code} - "
            f"Time: {process_time:.3f}s"
        )
        return response
    except Exception as e:
        process_time = time.time() - start_time
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
if settings.rate_limit_enabled:
    storage_uri = "memory://"  
    if settings.redis_url.startswith("redis://"):
        try:
            import redis
            test_redis = redis.from_url(settings.redis_url, socket_connect_timeout=0.5, socket_timeout=0.5)
            test_redis.ping()
            storage_uri = settings.redis_url
            test_redis.close()
            logger.info("Redis подключен, используется для rate limiting")
        except Exception as e:
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
def get_cors_origins():
    dev_origins = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ]
    prod_default_origins = [
        "https://xtrix.vercel.app",
        "https://xtrix-production.vercel.app",
    ]
    env_origins = settings.get_cors_origins_list()
    if settings.environment == "production":
        return list(set(prod_default_origins + env_origins)) if env_origins else prod_default_origins
    else:
        return list(set(dev_origins + env_origins))  
if settings.environment == "production":
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
        max_age=3600,  
    )
else:
    import re
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",  
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
        max_age=3600,  
    )
@app.get("/")
async def root():
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
app.include_router(client_relations.router, prefix="/client-relations", tags=["client-relations"])
app.include_router(employees.router, prefix="/employees", tags=["employees"])
app.include_router(businesses.router, prefix="/businesses", tags=["businesses"])
app.include_router(products.router, prefix="/products", tags=["products"])
app.include_router(expenses.router, prefix="/expenses", tags=["expenses"])
app.include_router(loyalty.router, prefix="/loyalty", tags=["loyalty"])
app.include_router(user_preferences.router, prefix="/user-preferences", tags=["user-preferences"])