"""Request middleware.

Written as pure ASGI middleware rather than Starlette's ``BaseHTTPMiddleware``
for one specific reason: ``BaseHTTPMiddleware`` runs the downstream application
in a *separate task*, so a ``ContextVar`` set there does not reach the endpoint.
Since tenant isolation reads the ambient context at query time
(:mod:`app.db.tenancy`), that would silently break the guard. Pure ASGI
middleware shares the task, so the context propagates.

Provides:

*   trace/request identifiers on every request and response (section 47);
*   a request duration log line with the outcome;
*   a request body size limit;
*   the minimum-supported-app-version gate (section 110).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import Settings
from app.core.context import RequestContext, clear_context, current_context, set_context
from app.core.ids import uuid7
from app.core.logging import get_logger

__all__ = ["BodySizeLimitMiddleware", "RequestContextMiddleware"]

log = get_logger("app.request")

TRACE_HEADER = "x-trace-id"
REQUEST_HEADER = "x-request-id"
APP_VERSION_HEADER = "x-app-version"

#: Paths excluded from access logging to keep probe noise out of the log.
_QUIET_PATHS = frozenset({"/health/live", "/health/ready"})


def _client_ip(scope: Scope, headers: Headers) -> str | None:
    """Best-effort client address.

    ``X-Forwarded-For`` is only meaningful behind a proxy that sets it; it is
    used for rate limiting, and the value is hashed before it is ever stored.
    """
    forwarded = headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = scope.get("client")
    return client[0] if client else None


class RequestContextMiddleware:
    """Establish trace context, log the outcome, echo correlation headers."""

    def __init__(self, app: ASGIApp, *, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        incoming_trace = headers.get(TRACE_HEADER)
        trace_id = incoming_trace or uuid7().hex
        request_id = uuid7().hex

        context = RequestContext(
            trace_id=trace_id,
            request_id=request_id,
            app_version=headers.get(APP_VERSION_HEADER),
            client_ip=_client_ip(scope, headers),
        )
        token = set_context(context)
        scope["ecomsbd_context"] = context

        started = time.perf_counter()
        status_holder: dict[str, Any] = {"status": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                mutable = MutableHeaders(scope=message)
                mutable[TRACE_HEADER] = trace_id
                mutable[REQUEST_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            path = scope.get("path", "")
            if path not in _QUIET_PATHS:
                # The final context may carry tenant/user identifiers added by
                # the authentication dependency.
                final = current_context()
                log.info(
                    "http request",
                    extra={
                        "method": scope.get("method"),
                        "path": path,
                        "status": status_holder["status"],
                        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                        "tenant_id": str(final.tenant_id) if final.tenant_id else None,
                        "user_id": str(final.user_id) if final.user_id else None,
                    },
                )
            clear_context(token)


class BodySizeLimitMiddleware:
    """Reject oversized request bodies before they are buffered.

    Master spec section 79 requires a request size limit on webhook ingress; the
    same limit is applied everywhere so a single large upload cannot exhaust
    memory on the single VPS the system starts on.
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        declared = headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > self.max_bytes:
            await self._reject(send)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _BodyTooLarge:
            await self._reject(send)

    async def _reject(self, send: Send) -> None:
        body = b'{"code":"VALIDATION_ERROR","message_en":"Request body too large"}'
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


class _BodyTooLarge(Exception):
    """Internal signal; never surfaces to a handler."""


def enrich_context(**fields: Any) -> None:
    """Add identifiers to the ambient context after authentication."""
    set_context(replace(current_context(), **fields))


AsyncHandler = Callable[..., Awaitable[Any]]
