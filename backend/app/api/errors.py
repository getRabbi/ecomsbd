"""Exception handlers.

Every response body follows :class:`~app.core.errors.ErrorResponse`, so the
mobile client has one shape to parse and one field (``code``) to branch on.

Three behaviours are deliberate:

*   an unexpected exception logs a stack trace and returns ``INTERNAL_ERROR``
    with no internal detail — the ``reference_id`` is how support correlates it;
*   a :class:`~app.core.errors.TenantIsolationError` returns a plain 404 to the
    client while logging the real cause at ``ERROR``. It is a P0 incident signal
    (master spec section 117), not a routine 403;
*   FastAPI validation errors are translated rather than passed through, so a
    client never sees two different error shapes.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.context import current_context
from app.core.errors import (
    AppError,
    ErrorCode,
    ErrorResponse,
    RateLimitedError,
    TenantIsolationError,
)
from app.core.logging import get_logger
from app.core.redaction import redact_value

__all__ = ["install_exception_handlers"]

log = get_logger("app.errors")

#: HTTP status -> error code, for exceptions raised outside the AppError family.
_STATUS_TO_CODE = {
    400: ErrorCode.VALIDATION_ERROR,
    401: ErrorCode.UNAUTHENTICATED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    409: ErrorCode.CONFLICT,
    413: ErrorCode.VALIDATION_ERROR,
    422: ErrorCode.VALIDATION_ERROR,
    426: ErrorCode.UNSUPPORTED_APP_VERSION,
    429: ErrorCode.RATE_LIMITED,
    503: ErrorCode.SERVICE_UNAVAILABLE,
}


def _reference_id() -> str:
    context = current_context()
    return context.request_id or context.trace_id


def _json(
    payload: ErrorResponse, status_code: int, headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json"),
        headers=headers,
    )


async def _handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
    reference = _reference_id()

    if isinstance(exc, TenantIsolationError):
        # Logged loudly; reported to the client as a plain "not found" so the
        # response cannot be used to probe another tenant's data.
        log.error(
            "cross-tenant access blocked",
            extra={**exc.audit_fields(), "reference_id": reference},
        )
    elif exc.http_status >= 500:
        log.exception("request failed", extra={"code": str(exc.code), "reference_id": reference})
    else:
        log.info(
            "request rejected",
            extra={
                "code": str(exc.code),
                "reference_id": reference,
                "details": redact_value(exc.details or {}),
            },
        )

    headers: dict[str, str] | None = None
    if isinstance(exc, RateLimitedError) and exc.retry_after_seconds:
        headers = {"retry-after": str(exc.retry_after_seconds)}

    return _json(exc.to_response(reference), exc.http_status, headers)


async def _handle_validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    fields: list[dict[str, Any]] = []
    for error in exc.errors():
        location = [str(part) for part in error.get("loc", []) if part not in ("body", "query")]
        fields.append(
            {
                "field": ".".join(location) or "body",
                "reason": error.get("msg", "invalid"),
                "type": error.get("type"),
            }
        )
    wrapped = AppError(
        "Request validation failed",
        code=ErrorCode.VALIDATION_ERROR,
        details={"fields": fields},
    )
    return _json(wrapped.to_response(_reference_id()), wrapped.http_status)


async def _handle_http_exception(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _STATUS_TO_CODE.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
    wrapped = AppError(str(exc.detail), code=code, http_status=exc.status_code)
    return _json(wrapped.to_response(_reference_id()), exc.status_code)


async def _handle_unexpected(_request: Request, exc: Exception) -> JSONResponse:
    reference = _reference_id()
    log.exception(
        "unhandled exception",
        extra={"reference_id": reference, "error_type": type(exc).__name__},
    )
    wrapped = AppError(code=ErrorCode.INTERNAL_ERROR)
    return _json(wrapped.to_response(reference), status.HTTP_500_INTERNAL_SERVER_ERROR)


def install_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _handle_app_error)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _handle_validation_error)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _handle_unexpected)
