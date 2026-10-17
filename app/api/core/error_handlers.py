import traceback
from typing import Dict, Any, Optional
from fastapi import Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from app.api.core.config import settings
from app.api.core.logger import logger
SECRET_HEADERS = {"authorization", "cookie", "x-apple-token", "x-google-token", "x-erl-token", "stripe-signature"}
SECRET_KEY_PARTS = ("password", "token", "secret", "totp")
def _redact(value: Any) -> Any:
    # error responses echo the request in detailed mode - never send credentials back
    if isinstance(value, dict):
        return {
            k: "***" if any(part in str(k).lower() for part in SECRET_KEY_PARTS) else _redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value
def create_error_response(
    status_code: int,
    message: str,
    detail: Optional[Dict[str, Any]] = None,
    error_type: Optional[str] = None,
    traceback_str: Optional[str] = None,
    request: Optional[Request] = None,
) -> JSONResponse:
    response_data: Dict[str, Any] = {
        "error": {
            "message": message,
            "status_code": status_code,
        }
    }
    if detail:
        response_data["error"]["detail"] = detail
    if error_type:
        response_data["error"]["type"] = error_type
    detailed_mode = settings.environment == "development" or getattr(settings, 'detailed_errors', True)
    if detailed_mode and request:
        try:
            request_info: Dict[str, Any] = {
                "method": request.method,
                "url": str(request.url.replace(query="")),
                "path": request.url.path,
                "query_params": _redact(dict(request.query_params)),
                "headers": {
                    k: "***" if k.lower() in SECRET_HEADERS else v
                    for k, v in request.headers.items()
                },
                "client": {
                    "host": request.client.host if request.client else None,
                    "port": request.client.port if request.client else None,
                },
            }
            try:
                if hasattr(request, '_body'):
                    body = request._body
                    if body:
                        import json
                        try:
                            request_info["body"] = _redact(json.loads(body.decode()))
                        except:
                            # not JSON, can't tell what's secret in there - leave it out
                            request_info["body"] = "<non-JSON body omitted>"
            except:
                pass
            response_data["error"]["request"] = request_info
        except Exception as e:
            response_data["error"]["request_error"] = str(e)
    detailed_mode = settings.environment == "development" or getattr(settings, 'detailed_errors', True)
    if detailed_mode and traceback_str:
        response_data["error"]["traceback"] = traceback_str.split("\n")
    return JSONResponse(
        status_code=status_code,
        content=response_data,
    )
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    errors = []
    for error in exc.errors():
        field_path = " -> ".join(str(loc) for loc in error.get("loc", []))
        error_detail = {
            "field": field_path,
            "message": error.get("msg"),
            "type": error.get("type"),
            "input": error.get("input"),
        }
        if error.get("type") == "value_error.missing":
            error_detail["hint"] = f"Field '{field_path}' is required"
        elif error.get("type") == "type_error":
            error_detail["hint"] = f"Field '{field_path}' has wrong type. Expected: {error.get('ctx', {}).get('expected_type', 'unknown')}"
        elif error.get("type") == "value_error":
            error_detail["hint"] = f"Field '{field_path}' has invalid value"
        errors.append(error_detail)
    logger.warning(
        f"Validation error: {request.method} {request.url.path} - "
        f"Errors: {len(errors)} - "
        f"Client: {request.client.host if request.client else 'unknown'}"
    )
    traceback_str = None
    detailed_mode = settings.environment == "development" or getattr(settings, 'detailed_errors', True)
    if detailed_mode:
        traceback_str = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return create_error_response(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        message=f"Validation error: {len(errors)} field(s) failed validation",
        detail={
            "validation_errors": errors,
            "total_errors": len(errors),
            "hint": "Check the 'validation_errors' array for details about each field",
        },
        error_type="ValidationError",
        traceback_str=traceback_str,
        request=request,
    )
async def database_exception_handler(
    request: Request,
    exc: SQLAlchemyError,
) -> JSONResponse:
    if isinstance(exc, IntegrityError):
        # duplicate barcode, email etc. - the client's fault, but keep constraint names and SQL to ourselves
        logger.warning(f"Integrity error: {request.method} {request.url.path} - {exc.orig}")
        return create_error_response(
            status_code=status.HTTP_409_CONFLICT,
            message="This conflicts with existing data (duplicate value?)",
            error_type="IntegrityError",
            request=request,
        )
    error_msg = str(exc)
    error_class = exc.__class__.__name__
    logger.error(
        f"Database error: {request.method} {request.url.path} - "
        f"Error: {error_msg} - "
        f"Type: {error_class}",
        exc_info=True,
    )
    traceback_str = None
    detailed_mode = settings.environment == "development" or getattr(settings, 'detailed_errors', True)
    if detailed_mode:
        traceback_str = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    detailed_mode = settings.environment == "development" or getattr(settings, 'detailed_errors', True)
    if not detailed_mode:
        message = "Database error occurred. Please contact support if this persists."
        detail = {
            "error_id": f"DB-{hash(error_msg) % 10000}",  
        }
    else:
        message = f"Database error: {error_msg}"
        detail = {
            "error_class": error_class,
            "sqlalchemy_error": error_msg,
            "hint": _get_database_error_hint(error_class, error_msg),
        }
    return create_error_response(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        message=message,
        detail=detail,
        error_type="DatabaseError",
        traceback_str=traceback_str,
        request=request,
    )
def _get_database_error_hint(error_class: str, error_msg: str) -> str:
    error_lower = error_msg.lower()
    if "connection" in error_lower or "timeout" in error_lower:
        return "Database connection issue. Check if database is running and accessible."
    elif "duplicate" in error_lower or "unique" in error_lower:
        return "Duplicate entry detected. The record already exists with these values."
    elif "foreign key" in error_lower or "constraint" in error_lower:
        return "Foreign key constraint violation. Referenced record does not exist."
    elif "not null" in error_lower:
        return "Required field is missing. Check that all required fields are provided."
    elif "syntax" in error_lower or "sql" in error_lower:
        return "SQL syntax error. This may indicate a bug in the query generation."
    else:
        return "Check database logs for more details."
async def general_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    error_msg = str(exc)
    error_type = exc.__class__.__name__
    logger.error(
        f"Unhandled exception: {request.method} {request.url.path} - "
        f"Type: {error_type} - "
        f"Error: {error_msg} - "
        f"Client: {request.client.host if request.client else 'unknown'}",
        exc_info=True,
    )
    traceback_str = None
    detailed_mode = settings.environment == "development" or getattr(settings, 'detailed_errors', True)
    if detailed_mode:
        traceback_str = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    detailed_mode = settings.environment == "development" or getattr(settings, 'detailed_errors', True)
    if not detailed_mode:
        message = "An internal error occurred. Please contact support if this persists."
        detail = {
            "error_id": f"ERR-{hash(error_msg) % 10000}",  
        }
    else:
        message = f"{error_type}: {error_msg}"
        detail = {
            "error_class": error_type,
            "error_message": error_msg,
            "module": getattr(exc, "__module__", "unknown"),
            "hint": _get_general_error_hint(error_type, error_msg),
        }
    return create_error_response(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        message=message,
        detail=detail,
        error_type=error_type,
        traceback_str=traceback_str,
        request=request,
    )
def _get_general_error_hint(error_type: str, error_msg: str) -> str:
    error_lower = error_msg.lower()
    if "import" in error_lower or "module" in error_lower:
        return "Import error. Check that all required modules are installed."
    elif "attribute" in error_lower:
        return "Attribute error. Object may not have the expected attribute or method."
    elif "key" in error_lower:
        return "Key error. Dictionary key may be missing."
    elif "index" in error_lower:
        return "Index error. List or array index may be out of range."
    elif "permission" in error_lower or "access" in error_lower:
        return "Permission error. Check file or resource permissions."
    else:
        return "Check the traceback for more details about the error location."
async def http_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    status_code = getattr(exc, "status_code", status.HTTP_500_INTERNAL_SERVER_ERROR)
    detail = getattr(exc, "detail", str(exc))
    logger.warning(
        f"HTTP exception: {request.method} {request.url.path} - "
        f"Status: {status_code} - "
        f"Detail: {detail} - "
        f"Client: {request.client.host if request.client else 'unknown'}"
    )
    hint = None
    if status_code == 404:
        hint = "The requested resource was not found. Check the URL and resource ID."
    elif status_code == 403:
        hint = "Access forbidden. You may not have permission to access this resource."
    elif status_code == 401:
        hint = "Authentication required. Please provide valid credentials."
    elif status_code == 400:
        hint = "Bad request. Check that all required parameters are provided correctly."
    elif status_code == 429:
        hint = "Rate limit exceeded. Please wait before making more requests."
    response_detail = detail if not isinstance(detail, str) else None
    detailed_mode = settings.environment == "development" or getattr(settings, 'detailed_errors', True)
    if detailed_mode and hint:
        if response_detail is None:
            response_detail = {}
        if isinstance(response_detail, dict):
            response_detail["hint"] = hint
    return create_error_response(
        status_code=status_code,
        message=detail if isinstance(detail, str) else "HTTP error occurred",
        detail=response_detail,
        error_type=exc.__class__.__name__,
        request=request,
    )
