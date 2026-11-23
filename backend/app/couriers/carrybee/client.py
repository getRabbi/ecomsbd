"""Carrybee HTTP client (V4 WP7.2).

Four documented calls (``docs/providers/carrybee/CONTRACT.md``): list stores,
create an order, read an order's details, cancel an order. Reads are retried a
few times on a provider-side failure; a write is sent **once** and any outcome
that is not a clear answer becomes ambiguous — Carrybee documents no
idempotency key, so a resend could make a second parcel.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from app.core.logging import get_logger
from app.core.redaction import redact_text
from app.couriers.carrybee.contract import (
    HEADER_CLIENT_CONTEXT,
    HEADER_CLIENT_ID,
    HEADER_CLIENT_SECRET,
    LIVE_BASE_URL,
    PATH_ORDER_CANCEL,
    PATH_ORDER_DETAILS,
    PATH_ORDERS,
    PATH_STORES,
    PROVIDER,
    SANDBOX_BASE_URL,
)
from app.couriers.http import (
    ProviderAmbiguousError,
    ProviderAuthError,
    ProviderError,
    ProviderErrorKind,
    ProviderProtocolError,
    ProviderRateLimitedError,
    ProviderTransport,
    ProviderUnavailableError,
    RawResponse,
    classify_http,
    new_correlation_id,
)

__all__ = [
    "CARRYBEE_PROVIDER",
    "CarrybeeCallResult",
    "CarrybeeClient",
    "CarrybeeConfig",
    "CarrybeeCredentials",
]

log = get_logger(__name__)

CARRYBEE_PROVIDER = PROVIDER


@dataclass(frozen=True, slots=True)
class CarrybeeCredentials:
    """One merchant's three Carrybee values, for one environment (CONTRACT.md §1)."""

    client_id: str
    client_secret: str
    client_context: str
    sandbox: bool = False

    def __repr__(self) -> str:
        return (
            "CarrybeeCredentials(client_id='[redacted]', client_secret='[redacted]', "
            f"client_context='[redacted]', sandbox={self.sandbox})"
        )

    __str__ = __repr__

    @property
    def masked_identifier(self) -> str:
        return f"****{self.client_id[-4:]}" if len(self.client_id) > 4 else "****"

    @property
    def cache_key(self) -> str:
        material = (
            f"{self.client_id}\x00{self.client_secret}\x00{self.client_context}"
            f"\x00{int(self.sandbox)}"
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CarrybeeConfig:
    """Client tuning. Carrybee publishes no rate limit or retry contract."""

    live_base_url: str = LIVE_BASE_URL
    sandbox_base_url: str = SANDBOX_BASE_URL
    #: Reads only. A write is never retried.
    max_read_retries: int = 2
    retry_backoff_seconds: float = 0.5

    def base_url(self, *, sandbox: bool) -> str:
        return (self.sandbox_base_url if sandbox else self.live_base_url).rstrip("/")


@dataclass(frozen=True, slots=True)
class CarrybeeCallResult:
    body: dict[str, Any]
    correlation_id: str
    http_status: int

    @property
    def data(self) -> Any:
        return self.body.get("data")


class CarrybeeClient:
    def __init__(
        self,
        transport: ProviderTransport,
        *,
        config: CarrybeeConfig | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._transport = transport
        self._config = config or CarrybeeConfig()
        self._sleep = sleep

    @property
    def config(self) -> CarrybeeConfig:
        return self._config

    async def list_stores(self, creds: CarrybeeCredentials) -> CarrybeeCallResult:
        return await self._call("GET", PATH_STORES, creds, idempotent=True)

    async def order_details(self, creds: CarrybeeCredentials, reference: str) -> CarrybeeCallResult:
        path = PATH_ORDER_DETAILS.format(reference=quote(reference, safe=""))
        return await self._call("GET", path, creds, idempotent=True)

    async def create_order(
        self, creds: CarrybeeCredentials, payload: dict[str, Any]
    ) -> CarrybeeCallResult:
        return await self._call("POST", PATH_ORDERS, creds, json_body=payload, idempotent=False)

    async def cancel_order(
        self, creds: CarrybeeCredentials, reference: str, reason: str
    ) -> CarrybeeCallResult:
        path = PATH_ORDER_CANCEL.format(reference=quote(reference, safe=""))
        return await self._call(
            "POST", path, creds, json_body={"cancellation_reason": reason}, idempotent=False
        )

    async def aclose(self) -> None:
        await self._transport.aclose()

    # -------------------------------------------------------------- core --

    async def _call(
        self,
        method: str,
        path: str,
        creds: CarrybeeCredentials,
        *,
        json_body: Any | None = None,
        idempotent: bool,
    ) -> CarrybeeCallResult:
        url = f"{self._config.base_url(sandbox=creds.sandbox)}{path}"
        attempts = self._config.max_read_retries + 1 if idempotent else 1
        correlation_id = new_correlation_id()
        for attempt in range(attempts):
            try:
                response = await self._transport.request(
                    method,
                    url,
                    headers=self._headers(creds, has_body=json_body is not None),
                    json_body=json_body,
                    correlation_id=correlation_id,
                    idempotent=idempotent,
                )
            except ProviderError as exc:
                if not idempotent or exc.is_auth_failure or attempt + 1 >= attempts:
                    raise
                await self._sleep(self._config.retry_backoff_seconds * (attempt + 1))
                continue
            parsed = self._interpret(method, path, response, idempotent=idempotent)
            if parsed is not None:
                return parsed
            if attempt + 1 < attempts:
                await self._sleep(self._config.retry_backoff_seconds * (attempt + 1))
        raise ProviderUnavailableError(
            "Carrybee is not answering successfully",
            provider=PROVIDER,
            reached_provider=True,
            correlation_id=correlation_id,
        )

    @staticmethod
    def _headers(creds: CarrybeeCredentials, *, has_body: bool) -> dict[str, str]:
        headers = {
            HEADER_CLIENT_ID: creds.client_id,
            HEADER_CLIENT_SECRET: creds.client_secret,
            HEADER_CLIENT_CONTEXT: creds.client_context,
            "Accept": "application/json",
        }
        if has_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _interpret(
        self, method: str, path: str, response: RawResponse, *, idempotent: bool
    ) -> CarrybeeCallResult | None:
        """A result, an exception, or ``None`` for a retryable read failure."""
        status = response.status_code
        common: dict[str, Any] = {
            "provider": PROVIDER,
            "http_status": status,
            "correlation_id": response.correlation_id,
        }
        if status >= 400:
            kind = classify_http(status)
            log.warning(
                "carrybee request refused",
                extra={
                    **common,
                    "operation": f"{method} {_template(path)}",
                    "provider_message": _provider_message(response.text),
                },
            )
            if kind is ProviderErrorKind.UNAVAILABLE and idempotent:
                return None
            if kind is ProviderErrorKind.AUTH:
                return _raise(ProviderAuthError(_MESSAGES[kind], reached_provider=True, **common))
            if kind is ProviderErrorKind.RATE_LIMITED:
                return _raise(
                    ProviderRateLimitedError(_MESSAGES[kind], reached_provider=True, **common)
                )
            if kind is ProviderErrorKind.UNAVAILABLE:
                # A 5xx answer to a write: the request certainly arrived.
                return _raise(
                    ProviderAmbiguousError("Carrybee failed while handling the request", **common)
                )
            return _raise(
                ProviderError(
                    _MESSAGES.get(kind, "Carrybee rejected the request."),
                    kind=kind,
                    reached_provider=True,
                    **common,
                )
            )
        if response.looks_like_html:
            return _raise(self._unreadable("Carrybee returned an HTML page", common, idempotent))
        try:
            body = json.loads(response.text) if response.text.strip() else None
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return _raise(self._unreadable("Carrybee's answer was not JSON", common, idempotent))
        if body.get("error") is not False:
            # CONTRACT.md §2: success carries "error": false. Anything else on a
            # 2xx contradicts the page; for a write that means "unknown".
            return _raise(
                self._unreadable("Carrybee's answer was not a success", common, idempotent)
            )
        return CarrybeeCallResult(
            body=body, correlation_id=response.correlation_id, http_status=status
        )

    @staticmethod
    def _unreadable(message: str, common: dict[str, Any], idempotent: bool) -> ProviderError:
        if idempotent:
            return ProviderProtocolError(message, reached_provider=True, **common)
        return ProviderAmbiguousError(message, **common)


def _raise(error: ProviderError) -> CarrybeeCallResult:
    raise error


def _template(path: str) -> str:
    """The path with any reference removed, for a log line."""
    parts = path.split("/")
    if len(parts) > 4 and parts[3] == "orders":
        parts[4] = "{reference}"
    return "/".join(parts)


def _provider_message(text: str) -> str | None:
    try:
        body = json.loads(text) if text.strip() else None
    except ValueError:
        return None
    if not isinstance(body, dict) or not isinstance(body.get("message"), str):
        return None
    return redact_text(body["message"].strip()[:200]) or None


_MESSAGES: dict[ProviderErrorKind, str] = {
    ProviderErrorKind.AUTH: "Carrybee rejected these credentials.",
    ProviderErrorKind.VALIDATION: "Carrybee rejected the request.",
    ProviderErrorKind.RATE_LIMITED: "Carrybee is rate limiting this account.",
    ProviderErrorKind.UNAVAILABLE: "Carrybee is not available right now.",
}
