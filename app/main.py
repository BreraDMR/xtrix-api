from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from slowapi.errors import RateLimitExceeded
from sqlalchemy.exc import SQLAlchemyError
import time
from app.api.core.config import settings
from app.api.core.logger import logger
from app.api.middleware.security_headers import SecurityHeadersMiddleware
from app.api.v1.endpoints import (
    clients, metrics, services, users, visits, files, subscriptions, schedules, 
    analytics, audit, archiving, specialist_profiles, client_relations, employees, businesses, products, expenses, loyalty, user_preferences,
    assistant, assistant_conversations, offers, onboarding, public_booking, questionnaires,
    questionnaire_answers,
)
from app.api.core import error_handlers
from app.api.middleware.rate_limit import DefaultRateLimitMiddleware, limiter, rate_limit_exceeded_handler
from app.api.middleware.trailing_slash import TrailingSlashMiddleware
app = FastAPI(
    title="Xtrix API",
    version="1.0.0",
    redirect_slashes=False,  # TrailingSlashMiddleware below takes both spellings, no 307
    docs_url=None,  # /docs is the Scalar page below
    redoc_url=None,
)
app.add_exception_handler(RequestValidationError, error_handlers.validation_exception_handler)
app.add_exception_handler(SQLAlchemyError, error_handlers.database_exception_handler)
# starlette's class, so the router's own 404/405 come out in our format too (FastAPI's is a subclass)
app.add_exception_handler(StarletteHTTPException, error_handlers.http_exception_handler)
app.add_exception_handler(Exception, error_handlers.general_exception_handler)
DOCS_PATHS = ("/docs", "/openapi.json")
@app.middleware("http")
async def protect_docs(request: Request, call_next):
    # in production the API reference sits behind HTTP Basic (a browser can't send a Bearer
    # header when you just open a page); no DOCS_USERNAME/DOCS_PASSWORD configured = no docs at all
    if request.url.path in DOCS_PATHS and settings.environment == "production":
        from fastapi.responses import JSONResponse, Response
        if not (settings.docs_username and settings.docs_password):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        if not _docs_credentials_ok(request.headers.get("Authorization", "")):
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Xtrix API docs"'})
    return await call_next(request)
def _docs_credentials_ok(header: str) -> bool:
    import base64
    import hmac
    if not header.startswith("Basic "):
        return False
    try:
        user, _, password = base64.b64decode(header[6:]).decode("utf-8").partition(":")
    except Exception:
        return False
    return hmac.compare_digest(user, settings.docs_username) & hmac.compare_digest(password, settings.docs_password)
@app.get("/docs", include_in_schema=False)
def api_reference():
    # Scalar instead of Swagger UI / ReDoc
    from scalar_fastapi import get_scalar_api_reference
    return get_scalar_api_reference(openapi_url=app.openapi_url, title="Xtrix API")
@app.middleware("http")
async def fix_proxy_headers(request: Request, call_next):
    if request.headers.get("X-Forwarded-Proto") == "https":
        request.scope["scheme"] = "https"
    elif request.headers.get("X-Forwarded-Ssl") == "on":
        request.scope["scheme"] = "https"
    response = await call_next(request)
    return response
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(TrailingSlashMiddleware, router=app.router)
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
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
app.add_middleware(DefaultRateLimitMiddleware)
def get_cors_origins():
    dev_origins = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ]
    env_origins = settings.get_cors_origins_list()
    if settings.environment == "production":
        # production origins come only from CORS_ORIGINS - hosting isn't hardcoded anymore
        return env_origins
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
        expose_headers=["X-Total-Count", "X-Page", "X-Per-Page", "Retry-After"],
        max_age=3600,  
    )
else:
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
        expose_headers=["X-Total-Count", "X-Page", "X-Per-Page", "Retry-After"],
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
app.include_router(assistant.router, prefix="/assistant", tags=["assistant"])
app.include_router(assistant_conversations.router, prefix="/assistant", tags=["assistant"])
app.include_router(offers.router, prefix="/offers", tags=["offers"])
app.include_router(onboarding.router, prefix="/onboarding", tags=["onboarding"])
app.include_router(public_booking.router, prefix="/public/booking", tags=["public-booking"])
app.include_router(public_booking.settings_router, prefix="/businesses/me/public-booking", tags=["businesses"])
app.include_router(questionnaires.router, prefix="/businesses/me/questionnaires", tags=["questionnaires"])
app.include_router(questionnaires.visit_router, prefix="/visits", tags=["questionnaires"])
app.include_router(questionnaire_answers.router, prefix="/public/questionnaires", tags=["public-questionnaires"])
app.include_router(questionnaire_answers.staff_router, prefix="/visits", tags=["questionnaires"])
