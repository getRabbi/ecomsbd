"""Provider-agnostic HTTP transport and error taxonomy.

This is the Steadfast transport and error taxonomy with the provider name
lifted out into a parameter. It exists because V2.1 adds two more providers,
and three copies of the reached-provider determination is three chances to get
the one rule wrong that the whole booking state machine rests on:

    **Could this request have created a parcel?**

A connect failure answers "no" — the bytes provably never left. A read timeout
answers "maybe" — the request went out and the answer was lost. Those two must
never share a code, because one is safe to retry and the other ships a second
parcel and bills the seller for both.

Steadfast's own :mod:`app.couriers.steadfast.transport` and
:mod:`app.couriers.steadfast.errors` are deliberately left untouched: V1 is
frozen and working, and converging it onto this module is a change with no
user-visible benefit and a live-money blast radius. New providers start here.

What this layer owns: timeouts, pooling, a response-size ceiling, correlation
ids, and the reached-provider determination. What it does not own: any
interpretation of the body. That is each provider client's job.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

import httpx

from app.core.errors import AppError, ErrorCode
from app.core.logging import get_logger

__all__ = [
    "MAX_RESPONSE_BYTES",
    "FakeProviderTransport",
    "HttpxProviderTransport",
    "ProviderAmbiguousError",
    "ProviderAuthError",
    "ProviderError",
    "ProviderErrorKind",
    "ProviderProtocolError",
    "ProviderRateLimitedError",
    "ProviderTransport",
    "ProviderUnavailableError",
    "ProviderValidationError",
    "RawResponse",
    "TransportTimeouts",
    "classify_http",
    "new_correlation_id",
    "to_app_error",
]

log = get_logger(__name__)

#: Bodies larger than this are refused unread. A courier response is a few
#: kilobytes; a megabyte of it is a proxy error page or a bug.
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


def new_correlation_id() -> str:
    """A short id tying a request, its log lines and its stored raw payload."""
    return uuid.uuid4().hex[:16]


# --------------------------------------------------------------- error kinds --


class ProviderErrorKind(StrEnum):
    """How a provider call failed, in ecomsbd's vocabulary."""

    AUTH = "INVALID_COURIER_CREDENTIALS"
    VALIDATION = "VALIDATION_ERROR"
    ADDRESS_REJECTED = "ADDRESS_REJECTED"
    RATE_LIMITED = "RATE_LIMITED"
    UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROTOCOL = "PROVIDER_PROTOCOL_ERROR"
    AMBIGUOUS = "BOOKING_AMBIGUOUS"
    UNKNOWN = "UNKNOWN_PROVIDER_ERROR"

    @property
    def is_deterministic(self) -> bool:
        """Whether re-sending the identical request would fail identically.

        Only these may move a courier account to ``NEEDS_RECONNECT`` or fail a
        booking safely. A transient error must never invalidate credentials.
        """
        return self in (
            ProviderErrorKind.AUTH,
            ProviderErrorKind.VALIDATION,
            ProviderErrorKind.ADDRESS_REJECTED,
        )

    @property
    def is_safe_for_write_retry(self) -> bool:
        """Whether a *create* may be re-sent after this. Never, by kind alone."""
        return False


_ERROR_CODE_FOR_KIND: dict[ProviderErrorKind, ErrorCode] = {
    ProviderErrorKind.AUTH: ErrorCode.INVALID_COURIER_CREDENTIALS,
    ProviderErrorKind.VALIDATION: ErrorCode.VALIDATION_ERROR,
    ProviderErrorKind.ADDRESS_REJECTED: ErrorCode.ADDRESS_REJECTED,
    ProviderErrorKind.RATE_LIMITED: ErrorCode.RATE_LIMITED,
    ProviderErrorKind.UNAVAILABLE: ErrorCode.COURIER_PROVIDER_UNAVAILABLE,
    ProviderErrorKind.PROTOCOL: ErrorCode.PROVIDER_PROTOCOL_ERROR,
    ProviderErrorKind.AMBIGUOUS: ErrorCode.BOOKING_AMBIGUOUS,
    ProviderErrorKind.UNKNOWN: ErrorCode.UNKNOWN_PROVIDER_ERROR,
}


class ProviderError(Exception):
    """A failed provider call.

    Carries no credential and no raw provider body. ``technical_context`` is
    the redacted detail support needs; it is stored, not shown to a seller.
    """

    kind: ProviderErrorKind = ProviderErrorKind.UNKNOWN

    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        kind: ProviderErrorKind | None = None,
        http_status: int | None = None,
        reached_provider: bool | None = None,
        correlation_id: str | None = None,
        technical_context: dict[str, Any] | None = None,
    ) -> None:
        self.kind = kind or type(self).kind
        self.provider = provider
        self.http_status = http_status
        #: ``True`` the request certainly arrived, ``False`` it certainly did
        #: not, ``None`` we cannot tell. ``None`` and ``True`` both forbid a
        #: create retry.
        self.reached_provider = reached_provider
        self.correlation_id = correlation_id
        self.technical_context = technical_context or {}
        super().__init__(message)

    @property
    def error_code(self) -> ErrorCode:
        return _ERROR_CODE_FOR_KIND[self.kind]

    @property
    def is_auth_failure(self) -> bool:
        return self.kind is ProviderErrorKind.AUTH

    @property
    def create_may_have_succeeded(self) -> bool:
        """Whether a create that raised this could have produced a parcel.

        The default is the unsafe answer. Only an explicit ``False`` — set when
        the transport proves the bytes never left, or when the provider
        positively rejected the payload — permits treating the create as a
        clean failure.
        """
        if self.reached_provider is False:
            return False
        return not self.kind.is_deterministic

    def as_log_context(self) -> dict[str, Any]:
        """Redaction-safe fields for a log line or a stored event."""
        return {
            "provider": self.provider,
            "error_kind": str(self.kind),
            "http_status": self.http_status,
            "reached_provider": self.reached_provider,
            "correlation_id": self.correlation_id,
            **self.technical_context,
        }


class ProviderAuthError(ProviderError):
    kind = ProviderErrorKind.AUTH


class ProviderValidationError(ProviderError):
    kind = ProviderErrorKind.VALIDATION


class ProviderRateLimitedError(ProviderError):
    kind = ProviderErrorKind.RATE_LIMITED


class ProviderUnavailableError(ProviderError):
    kind = ProviderErrorKind.UNAVAILABLE


class ProviderProtocolError(ProviderError):
    """The response was not the documented shape.

    Distinct from ``UNAVAILABLE`` because it usually means the request reached
    *something* — so a create that ends here is ambiguous, not failed.
    """

    kind = ProviderErrorKind.PROTOCOL


class ProviderAmbiguousError(ProviderError):
    """The outcome is genuinely unknown. Never retried automatically."""

    kind = ProviderErrorKind.AMBIGUOUS

    def __init__(self, message: str, **kwargs: Any) -> None:
        kwargs.setdefault("reached_provider", None)
        super().__init__(message, **kwargs)


def classify_http(status_code: int) -> ProviderErrorKind:
    """Classify an HTTP status the provider actually returned.

    The provider answered, so ``reached_provider`` is ``True`` at every call
    site that uses this — which is why a 5xx here becomes ``UNAVAILABLE`` for a
    read and is escalated to ambiguous by the *caller* for a write, rather than
    being decided here without knowing the operation.
    """
    if status_code in (401, 403):
        return ProviderErrorKind.AUTH
    if status_code == 404:
        # Never read as "the parcel does not exist": a 404 on a lookup is
        # handled by the caller as "no answer", which is what keeps booking
        # recovery from concluding absence from a missing record.
        return ProviderErrorKind.VALIDATION
    if status_code == 422:
        return ProviderErrorKind.VALIDATION
    if status_code == 429:
        return ProviderErrorKind.RATE_LIMITED
    if 400 <= status_code < 500:
        return ProviderErrorKind.VALIDATION
    if 500 <= status_code < 600:
        return ProviderErrorKind.UNAVAILABLE
    return ProviderErrorKind.UNKNOWN


def to_app_error(error: ProviderError) -> AppError:
    """Convert to the client-facing error.

    The provider's technical detail does not cross this boundary: the seller
    gets a stable code and Bangla copy from the existing catalogue, and the
    detail is kept for support.
    """
    details: dict[str, Any] = {"provider": error.provider}
    if error.kind is ProviderErrorKind.AMBIGUOUS:
        details["do_not_retry"] = True
    return AppError(
        str(error),
        code=error.error_code,
        details=details,
        retryable=False if error.create_may_have_succeeded else None,
    )


# ----------------------------------------------------------------- transport --


@dataclass(frozen=True, slots=True)
class TransportTimeouts:
    """Four separate budgets, because they mean different things.

    ``connect`` is the only one whose expiry proves nothing was sent, which is
    why it is short and separate: a generous connect timeout would turn
    provably-safe failures into ambiguous ones and leave parcels stuck in
    ``BOOKING_UNKNOWN`` that never needed to be.
    """

    connect: float = 5.0
    read: float = 20.0
    write: float = 10.0
    pool: float = 5.0

    def as_httpx(self) -> httpx.Timeout:
        return httpx.Timeout(connect=self.connect, read=self.read, write=self.write, pool=self.pool)


@dataclass(frozen=True, slots=True)
class RawResponse:
    """What came back, before anyone decided what it means."""

    status_code: int
    #: Decoded text, truncated at :data:`MAX_RESPONSE_BYTES`.
    text: str
    headers: dict[str, str] = field(default_factory=dict)
    correlation_id: str = ""
    elapsed_ms: int = 0

    @property
    def looks_like_html(self) -> bool:
        """Whether this is a proxy error page rather than the documented JSON."""
        content_type = self.headers.get("content-type", "").lower()
        if "html" in content_type:
            return True
        head = self.text.lstrip()[:200].lower()
        return head.startswith("<!doctype html") or head.startswith("<html")


class ProviderTransport(Protocol):
    """The port. One method, deliberately: everything else is the client's."""

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body: Any | None = None,
        correlation_id: str,
        idempotent: bool,
    ) -> RawResponse:
        """Perform one HTTP call.

        ``idempotent`` says whether the *operation* is safe to have been
        delivered twice. It never causes a retry here — this transport retries
        nothing — but it decides how an ambiguous failure is reported, which is
        what the booking state machine keys off.
        """
        ...

    async def aclose(self) -> None: ...


class HttpxProviderTransport:
    """Production transport over a pooled ``httpx.AsyncClient``."""

    def __init__(
        self,
        *,
        provider: str,
        timeouts: TransportTimeouts | None = None,
        max_connections: int = 10,
        max_keepalive: int = 5,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._provider = provider
        self._timeouts = timeouts or TransportTimeouts()
        self._client = client or httpx.AsyncClient(
            timeout=self._timeouts.as_httpx(),
            limits=httpx.Limits(
                max_connections=max_connections,
                max_keepalive_connections=max_keepalive,
            ),
            # Redirects are refused rather than followed: a redirect on a
            # create is an unexplained change of endpoint, and following it
            # would post merchant credentials somewhere the contract never
            # named.
            follow_redirects=False,
        )

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body: Any | None = None,
        correlation_id: str,
        idempotent: bool,
    ) -> RawResponse:
        try:
            response = await self._client.request(method, url, headers=headers, json=json_body)
        except httpx.ConnectError as exc:
            # No connection was established. The request provably never
            # arrived, so even a create is a clean failure.
            raise ProviderUnavailableError(
                "Could not connect to the courier",
                provider=self._provider,
                reached_provider=False,
                correlation_id=correlation_id,
                technical_context={"transport_error": type(exc).__name__},
            ) from exc
        except httpx.ConnectTimeout as exc:
            raise ProviderUnavailableError(
                "Timed out connecting to the courier",
                provider=self._provider,
                reached_provider=False,
                correlation_id=correlation_id,
                technical_context={"transport_error": "ConnectTimeout"},
            ) from exc
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as exc:
            # The bytes may have gone out and the answer been lost. For a read
            # that is merely unavailable; for a write it is the ambiguous case
            # the whole booking state machine exists for.
            raise self._ambiguous_or_unavailable(
                "The courier did not answer in time",
                idempotent=idempotent,
                correlation_id=correlation_id,
                context={"transport_error": type(exc).__name__},
            ) from exc
        except httpx.RemoteProtocolError as exc:
            raise self._ambiguous_or_unavailable(
                "The connection to the courier was reset",
                idempotent=idempotent,
                correlation_id=correlation_id,
                context={"transport_error": "RemoteProtocolError"},
            ) from exc
        except httpx.HTTPError as exc:
            raise self._ambiguous_or_unavailable(
                "The courier request failed",
                idempotent=idempotent,
                correlation_id=correlation_id,
                context={"transport_error": type(exc).__name__},
            ) from exc

        content = response.content
        if len(content) > MAX_RESPONSE_BYTES:
            raise ProviderProtocolError(
                "The courier returned an unreasonably large response",
                provider=self._provider,
                http_status=response.status_code,
                reached_provider=True,
                correlation_id=correlation_id,
                technical_context={"response_bytes": len(content)},
            )

        elapsed_ms = int(response.elapsed.total_seconds() * 1000)
        log.info(
            "courier request",
            extra={
                "provider": self._provider,
                "operation": f"{method} {_path_of(url)}",
                "http_status": response.status_code,
                "duration_ms": elapsed_ms,
                "correlation_id": correlation_id,
                "response_bytes": len(content),
            },
        )
        return RawResponse(
            status_code=response.status_code,
            text=response.text,
            headers={k.lower(): v for k, v in response.headers.items()},
            correlation_id=correlation_id,
            elapsed_ms=elapsed_ms,
        )

    def _ambiguous_or_unavailable(
        self,
        message: str,
        *,
        idempotent: bool,
        correlation_id: str,
        context: dict[str, Any],
    ) -> ProviderError:
        if idempotent:
            return ProviderUnavailableError(
                message,
                provider=self._provider,
                reached_provider=None,
                correlation_id=correlation_id,
                technical_context=context,
            )
        return ProviderAmbiguousError(
            message,
            provider=self._provider,
            correlation_id=correlation_id,
            technical_context=context,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def _path_of(url: str) -> str:
    """The path of a URL, for a log line that must not carry a query string."""
    from urllib.parse import urlparse

    return urlparse(url).path or "/"


# ---------------------------------------------------------------- test double --


@dataclass(slots=True)
class _ScriptedCall:
    status_code: int
    body: str
    headers: dict[str, str] = field(default_factory=dict)
    error: ProviderError | None = None


class FakeProviderTransport:
    """Scripted transport, so CI never depends on a courier being up.

    Keyed on ``METHOD /path``. A path registered with :meth:`always` answers
    every time; one registered with :meth:`enqueue` answers once, which is what
    lets a test assert that a create was sent exactly once.
    """

    def __init__(self, *, provider: str = "fake", base_url: str | None = None) -> None:
        self.provider = provider
        self._base_url = (base_url or "").rstrip("/")
        self._queued: dict[str, list[_ScriptedCall]] = {}
        self._always: dict[str, _ScriptedCall] = {}
        self.calls: list[dict[str, Any]] = []

    def _normalize(self, path: str) -> str:
        if self._base_url and path.startswith(self._base_url):
            path = path[len(self._base_url) :]
        if "://" in path:
            path = _path_of(path)
        return path.split("?", 1)[0] or "/"

    def enqueue(
        self,
        method: str,
        path: str,
        *,
        status_code: int = 200,
        body: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        key = _key(method, self._normalize(path))
        self._queued.setdefault(key, []).append(
            _ScriptedCall(status_code, body, headers or {"content-type": "application/json"})
        )

    def enqueue_error(self, method: str, path: str, error: ProviderError) -> None:
        key = _key(method, self._normalize(path))
        self._queued.setdefault(key, []).append(_ScriptedCall(0, "", {}, error))

    def always(
        self,
        method: str,
        path: str,
        *,
        status_code: int = 200,
        body: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        key = _key(method, self._normalize(path))
        self._always[key] = _ScriptedCall(
            status_code, body, headers or {"content-type": "application/json"}
        )

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body: Any | None = None,
        correlation_id: str,
        idempotent: bool,
    ) -> RawResponse:
        path = self._normalize(url)
        key = _key(method, path)
        self.calls.append(
            {
                "method": method.upper(),
                "path": path,
                "url": url,
                "headers": dict(headers),
                "json": json_body,
                "correlation_id": correlation_id,
                "idempotent": idempotent,
            }
        )

        scripted: _ScriptedCall | None = None
        queued = self._queued.get(key)
        if queued:
            scripted = queued.pop(0)
        elif key in self._always:
            scripted = self._always[key]

        if scripted is None:
            raise AssertionError(f"FakeProviderTransport has no scripted response for {key}")
        if scripted.error is not None:
            raise scripted.error

        return RawResponse(
            status_code=scripted.status_code,
            text=scripted.body,
            headers={k.lower(): v for k, v in scripted.headers.items()},
            correlation_id=correlation_id,
            elapsed_ms=1,
        )

    async def aclose(self) -> None:
        return None

    def calls_to(self, method: str, path: str) -> list[dict[str, Any]]:
        target = _key(method, self._normalize(path))
        return [call for call in self.calls if _key(call["method"], call["path"]) == target]

    @property
    def unconsumed(self) -> int:
        return sum(len(pending) for pending in self._queued.values())


def _key(method: str, path: str) -> str:
    return f"{method.upper()} {path}"
