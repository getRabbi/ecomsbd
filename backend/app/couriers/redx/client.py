"""Typed access to RedX's documented Open API.

Simpler than the Pathao client in the one way that matters: RedX has no login
call. The merchant's token *is* the credential, sent on every request as
``API-ACCESS-TOKEN: Bearer <token>``, so there is no token cache and nothing
that can expire mid-booking on our side.

Everything about retries follows the house rule: a read may be retried, a
write never is, whatever the configuration says. Two writes exist here — the
parcel create and the parcel update used to cancel — and both are sent exactly
once.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

from app.core.logging import get_logger
from app.core.redaction import redact_text
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
from app.couriers.redx.contract import (
    CANCEL_ENTITY_TYPE,
    CANCEL_PROPERTY,
    CANCEL_VALUE,
    ENDPOINT_METHODS,
    HEADER_AUTH,
    LIVE_BASE_URL,
    PROVIDER,
    SAFE_TO_RETRY,
    SANDBOX_BASE_URL,
    Endpoint,
    bearer,
)
from app.couriers.redx.dto import (
    parse_areas,
    parse_charge,
    parse_create_parcel,
    parse_parcel_info,
    parse_pickup_store,
    parse_pickup_stores,
    parse_tracking,
    parse_update,
)

__all__ = [
    "REDX_PROVIDER",
    "RedxCallResult",
    "RedxClient",
    "RedxConfig",
    "RedxCredentials",
]

log = get_logger(__name__)

REDX_PROVIDER = PROVIDER


@dataclass(frozen=True, slots=True)
class RedxCredentials:
    """One merchant's RedX API token, for one environment.

    ``__repr__`` is overridden and ``__str__`` follows it, so the token cannot
    reach a log line or a traceback by being interpolated into a message.
    """

    api_token: str
    #: Chosen at connect time. RedX issues a separate token for its sandbox,
    #: so a sandbox token only ever works against the sandbox host.
    sandbox: bool = False

    def __repr__(self) -> str:
        return f"RedxCredentials(api_token='[redacted]', sandbox={self.sandbox})"

    __str__ = __repr__

    @property
    def masked_identifier(self) -> str:
        """A stable, non-reversible hint the seller can recognise."""
        if len(self.api_token) <= 4:
            return "****"
        return f"****{self.api_token[-4:]}"

    @property
    def cache_key(self) -> str:
        """A digest that identifies these credentials without containing them."""
        material = f"{self.api_token}\x00{int(self.sandbox)}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class RedxConfig:
    """Client tuning. Nothing here is a claim about RedX.

    RedX publishes no rate limit and no retry contract, so every value is
    ecomsbd's own conservative policy.
    """

    live_base_url: str = LIVE_BASE_URL
    sandbox_base_url: str = SANDBOX_BASE_URL
    #: Reads only. A write is never retried, whatever this says.
    max_read_retries: int = 2
    retry_backoff_seconds: float = 0.5

    def base_url(self, *, sandbox: bool) -> str:
        return (self.sandbox_base_url if sandbox else self.live_base_url).rstrip("/")

    def url_for(
        self,
        endpoint: Endpoint,
        *,
        sandbox: bool,
        reference: str | None = None,
        query: dict[str, Any] | None = None,
    ) -> str:
        path = (
            endpoint.with_reference(quote(reference, safe=""))
            if reference is not None
            else str(endpoint)
        )
        url = f"{self.base_url(sandbox=sandbox)}{path}"
        params = {key: value for key, value in (query or {}).items() if value is not None}
        return f"{url}?{urlencode(params)}" if params else url


@dataclass(frozen=True, slots=True)
class RedxCallResult:
    """A parsed response plus the evidence trail behind it."""

    value: Any
    correlation_id: str
    http_status: int
    raw_body: Any
    endpoint: str
    elapsed_ms: int = 0


class RedxClient:
    """RedX's documented merchant surface, typed."""

    provider = REDX_PROVIDER

    def __init__(
        self,
        transport: ProviderTransport,
        *,
        config: RedxConfig | None = None,
        sleep: Callable[[float], Any] | None = None,
    ) -> None:
        self._transport = transport
        self._config = config or RedxConfig()
        self._sleep = sleep or asyncio.sleep

    @property
    def config(self) -> RedxConfig:
        return self._config

    # -------------------------------------------------------------- reads --

    async def list_pickup_stores(self, creds: RedxCredentials) -> RedxCallResult:
        """``GET /pickup/stores``. Also the credential check: see the adapter."""
        result = await self._call(Endpoint.PICKUP_STORES, creds)
        stores = parse_pickup_stores(result.raw_body)
        if stores is None:
            raise self._unreadable(result, "RedX's pickup store list could not be read")
        return _with_value(result, stores)

    async def pickup_store_info(self, creds: RedxCredentials, store_id: str) -> RedxCallResult:
        result = await self._call(Endpoint.PICKUP_STORE_INFO, creds, reference=store_id)
        store = parse_pickup_store(result.raw_body)
        if store is None:
            raise self._unreadable(result, "RedX's pickup store details could not be read")
        return _with_value(result, store)

    async def list_areas(
        self,
        creds: RedxCredentials,
        *,
        district_name: str | None = None,
        post_code: int | None = None,
    ) -> RedxCallResult:
        """``GET /areas``, optionally narrowed by one documented filter."""
        query: dict[str, Any] = {}
        if post_code is not None:
            query["post_code"] = post_code
        elif district_name:
            query["district_name"] = district_name
        result = await self._call(Endpoint.AREAS, creds, query=query)
        if not isinstance(result.raw_body, dict) or not isinstance(
            result.raw_body.get("areas"), list
        ):
            raise self._unreadable(result, "RedX's area list could not be read")
        return _with_value(result, parse_areas(result.raw_body))

    async def parcel_info(self, creds: RedxCredentials, tracking_id: str) -> RedxCallResult:
        result = await self._call(Endpoint.PARCEL_INFO, creds, reference=tracking_id)
        parcel = parse_parcel_info(result.raw_body)
        if parcel is None:
            raise self._unreadable(result, "RedX's parcel details could not be read")
        return _with_value(result, parcel)

    async def track_parcel(self, creds: RedxCredentials, tracking_id: str) -> RedxCallResult:
        result = await self._call(Endpoint.TRACK_PARCEL, creds, reference=tracking_id)
        entries = parse_tracking(result.raw_body)
        if entries is None:
            raise self._unreadable(result, "RedX's tracking history could not be read")
        return _with_value(result, entries)

    async def charge(
        self,
        creds: RedxCredentials,
        *,
        delivery_area_id: int,
        pickup_area_id: int,
        cash_collection_amount: int,
        weight_grams: int,
    ) -> RedxCallResult:
        """``GET /charge/charge_calculator``. A read; it creates nothing."""
        result = await self._call(
            Endpoint.CHARGE_CALCULATOR,
            creds,
            query={
                "delivery_area_id": delivery_area_id,
                "pickup_area_id": pickup_area_id,
                "cash_collection_amount": cash_collection_amount,
                "weight": weight_grams,
            },
        )
        charge = parse_charge(result.raw_body)
        if charge is None:
            raise self._unreadable(result, "RedX's delivery charge could not be read")
        return _with_value(result, charge)

    # ------------------------------------------------------------- writes --

    async def create_parcel(
        self, creds: RedxCredentials, payload: dict[str, Any]
    ) -> RedxCallResult:
        """``POST /parcel``. Sent exactly once, never retried."""
        result = await self._call(Endpoint.CREATE_PARCEL, creds, json_body=payload)
        tracking_id = parse_create_parcel(result.raw_body)
        if tracking_id is None:
            # RedX accepted the request and told us nothing we can act on. The
            # parcel may exist, so this is the ambiguous case, not a failure —
            # and it must never be re-sent.
            raise ProviderAmbiguousError(
                "RedX accepted the booking but returned no tracking id",
                provider=REDX_PROVIDER,
                http_status=result.http_status,
                correlation_id=result.correlation_id,
            )
        return _with_value(result, tracking_id)

    async def cancel_parcel(
        self, creds: RedxCredentials, tracking_id: str, *, reason: str | None = None
    ) -> RedxCallResult:
        """``PATCH /parcels`` setting ``status`` to ``cancelled``. Sent once.

        The value is ``(success, message)``. ``success`` is ``None`` when the
        body carries no boolean, which the caller must treat as "not known".
        """
        update: dict[str, Any] = {"property_name": CANCEL_PROPERTY, "new_value": CANCEL_VALUE}
        if reason:
            update["reason"] = reason[:200]
        result = await self._call(
            Endpoint.UPDATE_PARCEL,
            creds,
            json_body={
                "entity_type": CANCEL_ENTITY_TYPE,
                "entity_id": tracking_id,
                "update_details": update,
            },
        )
        return _with_value(result, parse_update(result.raw_body))

    async def aclose(self) -> None:
        await self._transport.aclose()

    # ---------------------------------------------------------- internals --

    async def _call(
        self,
        endpoint: Endpoint,
        creds: RedxCredentials,
        *,
        reference: str | None = None,
        query: dict[str, Any] | None = None,
        json_body: Any | None = None,
    ) -> RedxCallResult:
        method = ENDPOINT_METHODS[endpoint]
        url = self._config.url_for(
            endpoint, sandbox=creds.sandbox, reference=reference, query=query
        )
        idempotent = endpoint in SAFE_TO_RETRY
        attempts = self._config.max_read_retries + 1 if idempotent else 1
        correlation_id = new_correlation_id()
        last_error: ProviderError | None = None

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
                # A write never gets here twice: `attempts` is 1.
                if not idempotent or exc.is_auth_failure:
                    raise
                last_error = exc
                if attempt + 1 < attempts:
                    await self._sleep(self._config.retry_backoff_seconds * (attempt + 1))
                    continue
                raise

            parsed = self._interpret(endpoint, response, idempotent=idempotent)
            if parsed is not None:
                return parsed

            last_error = ProviderUnavailableError(
                "RedX is not answering successfully",
                provider=REDX_PROVIDER,
                http_status=response.status_code,
                reached_provider=True,
                correlation_id=correlation_id,
            )
            if attempt + 1 < attempts:
                await self._sleep(self._config.retry_backoff_seconds * (attempt + 1))
                continue
            raise last_error

        raise last_error or ProviderUnavailableError(  # pragma: no cover - loop returns
            "The RedX request did not complete",
            provider=REDX_PROVIDER,
            correlation_id=correlation_id,
        )

    def _headers(self, creds: RedxCredentials, *, has_body: bool) -> dict[str, str]:
        headers = {HEADER_AUTH: bearer(creds.api_token), "Accept": "application/json"}
        if has_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _interpret(
        self, endpoint: Endpoint, response: RawResponse, *, idempotent: bool
    ) -> RedxCallResult | None:
        """Turn a raw response into a result, an exception, or ``None`` to retry.

        ``None`` is returned only for a retryable read failure. Every other
        outcome either succeeds or raises here, so the retry loop cannot
        accidentally re-send something it should not.
        """
        status = response.status_code

        if status >= 400:
            kind = classify_http(status)
            log.warning(
                "redx request refused",
                extra={
                    "provider": REDX_PROVIDER,
                    "operation": f"{ENDPOINT_METHODS[endpoint]} {endpoint}",
                    "http_status": status,
                    "correlation_id": response.correlation_id,
                    # RedX's own words, trimmed and scrubbed. Its documented
                    # error bodies carry a message and a status code and
                    # nothing else, but this is a log line, so it is redacted
                    # like any other free text.
                    "provider_message": _provider_message(response.text),
                },
            )
            if kind is ProviderErrorKind.UNAVAILABLE and idempotent:
                return None  # retryable
            raise self._error_for(kind, response, idempotent=idempotent)

        if response.looks_like_html:
            raise self._protocol_or_ambiguous(
                "RedX returned an HTML page instead of JSON", response, idempotent=idempotent
            )

        try:
            body = json.loads(response.text) if response.text.strip() else None
        except ValueError as exc:
            raise self._protocol_or_ambiguous(
                "RedX's response was not valid JSON", response, idempotent=idempotent
            ) from exc

        if body is None:
            raise self._protocol_or_ambiguous(
                "RedX returned an empty body", response, idempotent=idempotent
            )

        return RedxCallResult(
            value=None,
            correlation_id=response.correlation_id,
            http_status=status,
            raw_body=body,
            endpoint=str(endpoint),
            elapsed_ms=response.elapsed_ms,
        )

    def _error_for(
        self, kind: ProviderErrorKind, response: RawResponse, *, idempotent: bool
    ) -> ProviderError:
        context: dict[str, Any] = {"endpoint_status": response.status_code}
        message = _MESSAGE_FOR_KIND[kind]
        common: dict[str, Any] = {
            "provider": REDX_PROVIDER,
            "http_status": response.status_code,
            "correlation_id": response.correlation_id,
            "technical_context": context,
        }

        if kind is ProviderErrorKind.AUTH:
            return ProviderAuthError(message, reached_provider=True, **common)
        if kind is ProviderErrorKind.RATE_LIMITED:
            return ProviderRateLimitedError(message, reached_provider=True, **common)
        if kind is ProviderErrorKind.UNAVAILABLE:
            # RedX answered with a 5xx, so it certainly received the request.
            # For a write that makes the outcome ambiguous, not failed.
            if not idempotent:
                return ProviderAmbiguousError("RedX failed while handling the request", **common)
            return ProviderUnavailableError(message, reached_provider=True, **common)
        return ProviderError(message, kind=kind, reached_provider=True, **common)

    def _protocol_or_ambiguous(
        self, message: str, response: RawResponse, *, idempotent: bool
    ) -> ProviderError:
        common: dict[str, Any] = {
            "provider": REDX_PROVIDER,
            "http_status": response.status_code,
            "correlation_id": response.correlation_id,
        }
        if idempotent:
            return ProviderProtocolError(message, reached_provider=True, **common)
        return ProviderAmbiguousError(message, **common)

    def _unreadable(self, result: RedxCallResult, message: str) -> ProviderProtocolError:
        """A 2xx read whose body is not the documented shape."""
        return ProviderProtocolError(
            message,
            provider=REDX_PROVIDER,
            http_status=result.http_status,
            reached_provider=True,
            correlation_id=result.correlation_id,
        )


_MESSAGE_FOR_KIND: dict[ProviderErrorKind, str] = {
    ProviderErrorKind.AUTH: "RedX rejected this API token.",
    ProviderErrorKind.VALIDATION: "RedX rejected the request.",
    ProviderErrorKind.ADDRESS_REJECTED: "RedX could not accept that address.",
    ProviderErrorKind.RATE_LIMITED: "RedX is rate limiting this account.",
    ProviderErrorKind.UNAVAILABLE: "RedX is not available right now.",
    ProviderErrorKind.PROTOCOL: "RedX's response could not be read.",
    ProviderErrorKind.AMBIGUOUS: "The outcome of the RedX request is unknown.",
    ProviderErrorKind.UNKNOWN: "The RedX request failed.",
}


def _provider_message(text: str) -> str | None:
    """RedX's ``message`` from an error body, trimmed and scrubbed for a log."""
    try:
        body = json.loads(text) if text.strip() else None
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    message = body.get("message")
    if not isinstance(message, str) or not message.strip():
        return None
    return redact_text(message.strip()[:200])


def _with_value(result: RedxCallResult, value: Any) -> RedxCallResult:
    return RedxCallResult(
        value=value,
        correlation_id=result.correlation_id,
        http_status=result.http_status,
        raw_body=result.raw_body,
        endpoint=result.endpoint,
        elapsed_ms=result.elapsed_ms,
    )
