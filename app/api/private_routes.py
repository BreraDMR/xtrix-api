"""Keep credential-bearing responses private, including validation and auth failures."""
from fastapi import Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.core import error_handlers
from app.api.middleware.rate_limit import rate_limit_exceeded_handler


class NoStoreRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def private_response(request: Request) -> Response:
            try:
                response = await original(request)
            except RequestValidationError as exc:
                response = await error_handlers.validation_exception_handler(request, exc)
            except RateLimitExceeded as exc:
                response = rate_limit_exceeded_handler(request, exc)
            except StarletteHTTPException as exc:
                # Dependency failures use the same no-store policy as successful reads.
                exc.headers = {**(exc.headers or {}), "Cache-Control": "no-store"}
                raise
            response.headers["Cache-Control"] = "no-store"
            return response

        return private_response

