"""HTTP transport for Steadfast.

Separated from :mod:`app.couriers.steadfast.client` for one reason: **CI must
never depend on Steadfast being up** (brief section 43). The client talks to a
:class:`SteadfastTransport` port; production wires
:class:`HttpxSteadfastTransport`, and the whole test suite wires
:class:`FakeSteadfastTransport` with fixtures recorded from the documentation.

What this layer owns:

*   strict, separate connect / read / write / pool timeouts;
*   a bounded connection pool, shared across requests;
*   a per-request correlation id, echoed into every log line;
*   a response-size ceiling, so a provider that starts streaming HTML cannot
    exhaust the single VPS this runs on;
*   **the reached-provider determination**, which is the whole safety story for
    a create. A connect failure proves the bytes never left; a read timeout
    proves nothing. Only this layer can tell them apart, so only this layer
    decides, and it never guesses in the safe direction.

What it does not own: any interpretation of the body. That is the client's job.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.core.logging import get_logger
from app.couriers.steadfast.contract import DEFAULT_BASE_URL
from app.couriers.steadfast.errors import (
    SteadfastAmbiguousError,
    SteadfastError,
    SteadfastProtocolError,
    SteadfastUnavailableError,
)

__all__ = [
    "FakeSteadfastTransport",
    "HttpxSteadfastTransport",
    "RawResponse",
    "SteadfastTransport",
    "TransportTimeouts",
    "new_correlation_id",
]

log = get_logger(__name__)

#: Bodies larger than this are refused unread. A courier response is a few
#: kilobytes; a megabyte of it is a proxy error page or a bug.
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


def new_correlation_id() -> str:
    """A short id tying a request, its log lines and its stored raw payload."""
    return uuid.uuid4().hex[:16]


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


class SteadfastTransport(Protocol):
    """The port. One method, deliberately: everything else is the client's."""

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body: dict[str, Any] | None = None,
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


class HttpxSteadfastTransport:
    """Production transport over a pooled ``httpx.AsyncClient``."""

    def __init__(
        self,
        *,
        timeouts: TransportTimeouts | None = None,
        max_connections: int = 10,
        max_keepalive: int = 5,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._timeouts = timeouts or TransportTimeouts()
        self._client = client or httpx.AsyncClient(
            timeout=self._timeouts.as_httpx(),
            limits=httpx.Limits(
                max_connections=max_connections,
                max_keepalive_connections=max_keepalive,
            ),
            # Redirects are refused rather than followed: a redirect on a
            # create is an unexplained change of endpoint, and following it
            # would post merchant credentials somewhere the document never
            # named.
            follow_redirects=False,
        )

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body: dict[str, Any] | None = None,
        correlation_id: str,
        idempotent: bool,
    ) -> RawResponse:
        try:
            response = await self._client.request(method, url, headers=headers, json=json_body)
        except httpx.ConnectError as exc:
            # No connection was established. The request provably never
            # arrived, so even a create is a clean failure.
            raise SteadfastUnavailableError(
                "Could not connect to the courier",
                reached_provider=False,
                correlation_id=correlation_id,
                technical_context={"transport_error": type(exc).__name__},
            ) from exc
        except httpx.ConnectTimeout as exc:
            raise SteadfastUnavailableError(
                "Timed out connecting to the courier",
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
            raise SteadfastProtocolError(
                "The courier returned an unreasonably large response",
                http_status=response.status_code,
                reached_provider=True,
                correlation_id=correlation_id,
                technical_context={"response_bytes": len(content)},
            )

        elapsed_ms = int(response.elapsed.total_seconds() * 1000)
        log.info(
            "steadfast request",
            extra={
                "provider": "steadfast",
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

    @staticmethod
    def _ambiguous_or_unavailable(
        message: str,
        *,
        idempotent: bool,
        correlation_id: str,
        context: dict[str, Any],
    ) -> SteadfastError:
        if idempotent:
            return SteadfastUnavailableError(
                message,
                reached_provider=None,
                correlation_id=correlation_id,
                technical_context=context,
            )
        return SteadfastAmbiguousError(
            message,
            correlation_id=correlation_id,
            technical_context=context,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def _path_of(url: str) -> str:
    """Path only. A full URL in a log line is noise, and a query could carry a reference."""
    try:
        return httpx.URL(url).path
    except Exception:  # pragma: no cover - defensive; URL was already used
        return "<unparseable>"


@dataclass
class _ScriptedCall:
    response: RawResponse | None
    error: SteadfastError | None


class FakeSteadfastTransport:
    """Scripted transport for tests and the fixture suite.

    Queues responses per ``"METHOD /path"`` key and records every call, so a
    test can assert both what came back *and* what was sent — which is how the
    "one failed bulk item does not resend the batch" tests are written.
    """

    def __init__(self, *, base_url: str | None = None) -> None:
        self._queues: dict[str, list[_ScriptedCall]] = {}
        self._default: _ScriptedCall | None = None
        self.calls: list[dict[str, Any]] = []
        self.closed = False
        # Tests script the *endpoint* path ("/create_order"), not the base
        # URL's path ("/api/v1/create_order"). Knowing the base lets the fake
        # normalize, so a test never has to repeat the deployment prefix and a
        # change of base URL does not rewrite every fixture.
        self._base_path = _path_of(base_url or DEFAULT_BASE_URL).rstrip("/")

    def _normalize(self, path: str) -> str:
        if self._base_path and path.startswith(self._base_path):
            return path[len(self._base_path) :] or "/"
        return path

    # ------------------------------------------------------------ scripting --

    def enqueue(
        self,
        method: str,
        path: str,
        *,
        status_code: int = 200,
        body: str = "",
        headers: dict[str, str] | None = None,
    ) -> FakeSteadfastTransport:
        self._queues.setdefault(_key(method, path), []).append(
            _ScriptedCall(
                response=RawResponse(
                    status_code=status_code,
                    text=body,
                    headers={"content-type": "application/json", **(headers or {})},
                ),
                error=None,
            )
        )
        return self

    def enqueue_error(
        self, method: str, path: str, error: SteadfastError
    ) -> FakeSteadfastTransport:
        self._queues.setdefault(_key(method, path), []).append(
            _ScriptedCall(response=None, error=error)
        )
        return self

    def always(
        self, *, status_code: int = 200, body: str = "", headers: dict[str, str] | None = None
    ) -> FakeSteadfastTransport:
        """Answer every unscripted call the same way."""
        self._default = _ScriptedCall(
            response=RawResponse(
                status_code=status_code,
                text=body,
                headers={"content-type": "application/json", **(headers or {})},
            ),
            error=None,
        )
        return self

    # -------------------------------------------------------------- the port --

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body: dict[str, Any] | None = None,
        correlation_id: str,
        idempotent: bool,
    ) -> RawResponse:
        path = self._normalize(_path_of(url))
        self.calls.append(
            {
                "method": method,
                "path": path,
                "json": json_body,
                "correlation_id": correlation_id,
                "idempotent": idempotent,
                # Recorded so a test can prove the auth headers were sent
                # without the test itself needing to hold a credential.
                "header_names": sorted(headers),
            }
        )

        queue = self._queues.get(_key(method, path))
        call = queue.pop(0) if queue else self._default
        if call is None:
            raise AssertionError(
                f"FakeSteadfastTransport has no scripted answer for {method} {path}"
            )
        if call.error is not None:
            raise call.error
        if call.response is None:  # pragma: no cover - scripting bug, not a code path
            raise AssertionError("scripted call has neither a response nor an error")
        # The fake returns exactly what it was scripted with. Deciding that
        # an HTML body or an odd status means something is the client's job,
        # and a fake that pre-judged it would test itself.
        response = call.response
        return RawResponse(
            status_code=response.status_code,
            text=response.text,
            headers=response.headers,
            correlation_id=correlation_id,
            elapsed_ms=1,
        )

    async def aclose(self) -> None:
        self.closed = True

    # -------------------------------------------------------------- asserts --

    def calls_to(self, method: str, path: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["method"] == method and c["path"] == path]

    @property
    def unconsumed(self) -> int:
        return sum(len(queue) for queue in self._queues.values())


def _key(method: str, path: str) -> str:
    return f"{method.upper()} {path}"
