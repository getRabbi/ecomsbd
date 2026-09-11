"""The Steadfast HTTP client.

One place in the whole codebase constructs a Steadfast URL, sets a Steadfast
header or reads a Steadfast body. Business services call
:class:`~app.couriers.steadfast.adapter.SteadfastAdapter`, which calls this;
nothing else does.

The client's own rules:

*   **It never retries a write.** :meth:`SteadfastClient.create_order` and
    :meth:`SteadfastClient.create_return_request` are sent exactly once. Reads
    may be retried, because a ``GET`` creates nothing — and even then only the
    documented number of times, with a pause, and never past the circuit
    breaker.
*   **It never returns a half-understood response.** A body that is not the
    documented shape raises :class:`SteadfastProtocolError` rather than a DTO
    with ``None`` where the id should be.
*   **It never puts a credential in an exception, a log line or a raw payload.**
    The auth headers are built inside :meth:`_headers` and are the only place
    the key and secret exist outside the vault.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from app.core.logging import get_logger
from app.couriers.steadfast.contract import (
    BODY_STATUS_OK,
    BULK_MAX_ITEMS_DOCUMENTED,
    DEFAULT_BASE_URL,
    ENDPOINT_METHODS,
    HEADER_API_KEY,
    HEADER_CONTENT_TYPE,
    HEADER_SECRET_KEY,
    SAFE_TO_RETRY,
    Endpoint,
)
from app.couriers.steadfast.dto import (
    BulkCreateResult,
    CreateOrderRequest,
    CreateOrderResponse,
    PaymentDetailResponse,
    PaymentListResponse,
    PoliceStationRecord,
    ReturnRequestRecord,
    StatusResponse,
    coerce_paisa,
)
from app.couriers.steadfast.errors import (
    SteadfastAmbiguousError,
    SteadfastAuthError,
    SteadfastError,
    SteadfastErrorKind,
    SteadfastProtocolError,
    SteadfastRateLimitedError,
    SteadfastUnavailableError,
    SteadfastValidationError,
    classify_http,
)
from app.couriers.steadfast.transport import (
    RawResponse,
    SteadfastTransport,
    new_correlation_id,
)

__all__ = [
    "StatusLookupKind",
    "SteadfastCallResult",
    "SteadfastClient",
    "SteadfastConfig",
    "SteadfastCredentials",
]

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SteadfastCredentials:
    """One merchant's key and secret, in memory, for the length of one call.

    ``__repr__`` is overridden and ``__str__`` follows it, so a credential
    cannot reach a log line or a traceback by being interpolated into a
    message — which is how they usually get there.
    """

    api_key: str
    secret_key: str

    def __repr__(self) -> str:
        return "SteadfastCredentials(api_key='[redacted]', secret_key='[redacted]')"

    __str__ = __repr__

    @property
    def masked_identifier(self) -> str:
        """A stable, non-reversible hint the seller can recognise.

        Last four characters of the key only. Enough to tell two accounts
        apart on a screen, useless to anyone who obtains it.
        """
        if len(self.api_key) <= 4:
            return "****"
        return f"****{self.api_key[-4:]}"


@dataclass(frozen=True, slots=True)
class SteadfastConfig:
    """Client tuning. Nothing here is a claim about the provider.

    Every value is ecomsbd's own policy. The document states no rate limit, no
    retry contract and no batch guidance beyond a 500-item ceiling, so these
    are chosen conservatively and are configurable.
    """

    base_url: str = DEFAULT_BASE_URL

    #: ecomsbd's batch size, deliberately far below the documented 500. A
    #: failed 500-item request is 500 parcels in an unknown state; a failed
    #: 50-item request is 50. The provider permitting more is not a reason to
    #: send more (brief section 11).
    bulk_chunk_size: int = 50

    #: Reads only. A create is never retried, whatever this says.
    max_read_retries: int = 2
    retry_backoff_seconds: float = 0.5

    def __post_init__(self) -> None:
        if not 1 <= self.bulk_chunk_size <= BULK_MAX_ITEMS_DOCUMENTED:
            raise ValueError(
                f"bulk_chunk_size must be between 1 and the documented "
                f"maximum of {BULK_MAX_ITEMS_DOCUMENTED}"
            )

    def url_for(self, endpoint: Endpoint, reference: str | None = None) -> str:
        path = endpoint.with_reference(reference) if reference is not None else str(endpoint)
        return f"{self.base_url.rstrip('/')}{path}"


class StatusLookupKind(StrEnum):
    """Which of the three documented status endpoints to use.

    The adapter exposes one normalized ``get_status``, but the three provider
    forms stay individually addressable here: recovering a ``BOOKING_UNKNOWN``
    specifically needs *by invoice*, because the invoice is the only reference
    that exists before the provider has answered.
    """

    CONSIGNMENT_ID = "consignment_id"
    INVOICE = "invoice"
    TRACKING_CODE = "tracking_code"


_STATUS_ENDPOINTS: dict[StatusLookupKind, Endpoint] = {
    StatusLookupKind.CONSIGNMENT_ID: Endpoint.STATUS_BY_CID,
    StatusLookupKind.INVOICE: Endpoint.STATUS_BY_INVOICE,
    StatusLookupKind.TRACKING_CODE: Endpoint.STATUS_BY_TRACKING_CODE,
}


@dataclass(frozen=True, slots=True)
class SteadfastCallResult:
    """A parsed response plus the evidence trail behind it.

    Every caller that persists something gets the correlation id, the HTTP
    status and the raw decoded body, because the raw-payload record and the
    provider event both need them and re-deriving them later is impossible.
    """

    value: Any
    correlation_id: str
    http_status: int
    raw_body: Any
    endpoint: str
    elapsed_ms: int = 0
    #: Set when the endpoint has no documented response schema, so the caller
    #: can record that its typed fields are inferred rather than specified.
    schema_is_undocumented: bool = False


class SteadfastClient:
    """Typed access to the documented Steadfast V1 surface."""

    provider = "steadfast"

    def __init__(
        self,
        transport: SteadfastTransport,
        *,
        config: SteadfastConfig | None = None,
        sleep: Callable[[float], Any] | None = None,
    ) -> None:
        self._transport = transport
        self._config = config or SteadfastConfig()
        self._sleep = sleep or asyncio.sleep

    @property
    def config(self) -> SteadfastConfig:
        return self._config

    # ------------------------------------------------------------- documented --

    async def create_order(
        self, creds: SteadfastCredentials, request: CreateOrderRequest
    ) -> SteadfastCallResult:
        """``POST /create_order``. Sent once, never retried.

        A failure raises. The *kind* of failure is what the booking state
        machine reads: a deterministic rejection is a clean failure, and
        anything else is ambiguous and must be reconciled by invoice rather
        than re-sent.
        """
        result = await self._call(
            Endpoint.CREATE_ORDER,
            creds,
            json_body=request.as_payload(),
        )
        body = result.raw_body
        if not isinstance(body, dict):
            raise SteadfastProtocolError(
                "The courier's create response was not an object",
                http_status=result.http_status,
                reached_provider=True,
                correlation_id=result.correlation_id,
            )
        try:
            parsed = CreateOrderResponse.parse(body)
        except (KeyError, TypeError, ValueError) as exc:
            # The provider answered 200 with a body we cannot read. The parcel
            # may well exist. This is the ambiguous case, not a failure.
            raise SteadfastAmbiguousError(
                "The courier's create response could not be read",
                http_status=result.http_status,
                correlation_id=result.correlation_id,
                technical_context={"parse_error": type(exc).__name__},
            ) from exc
        return replace(result, value=parsed)

    async def create_order_bulk(
        self, creds: SteadfastCredentials, requests: Sequence[CreateOrderRequest]
    ) -> SteadfastCallResult:
        """``POST /create_order/bulk-order``. One chunk. Sent once, never retried.

        Chunking is the caller's responsibility — see
        :meth:`SteadfastAdapter.create_bulk` — because only the caller can
        persist a booking attempt per chunk before the request goes out.
        """
        if not requests:
            raise ValueError("create_order_bulk called with no items")
        if len(requests) > BULK_MAX_ITEMS_DOCUMENTED:
            raise ValueError(
                f"Steadfast documents a maximum of {BULK_MAX_ITEMS_DOCUMENTED} items per bulk request"
            )

        # The document's own example passes `data` as a JSON-encoded *string*
        # inside the request body. That is the only description given, so it is
        # what we send.
        payload = {"data": json.dumps([item.as_payload() for item in requests])}

        result = await self._call(Endpoint.CREATE_ORDER_BULK, creds, json_body=payload)
        try:
            parsed = BulkCreateResult.parse(result.raw_body)
        except (TypeError, ValueError) as exc:
            raise SteadfastAmbiguousError(
                "The courier's bulk response could not be read",
                http_status=result.http_status,
                correlation_id=result.correlation_id,
                technical_context={"parse_error": type(exc).__name__},
            ) from exc
        return replace(result, value=parsed)

    async def get_status(
        self,
        creds: SteadfastCredentials,
        reference: str,
        *,
        kind: StatusLookupKind = StatusLookupKind.CONSIGNMENT_ID,
    ) -> SteadfastCallResult:
        """One of the three documented status lookups.

        The three forms stay individually addressable: recovery from a
        ``BOOKING_UNKNOWN`` specifically needs *by invoice*, because the
        invoice is the only reference that exists before the provider has
        answered.
        """
        endpoint = _STATUS_ENDPOINTS[kind]
        result = await self._call(endpoint, creds, reference=reference)
        body = result.raw_body
        if not isinstance(body, dict):
            raise SteadfastProtocolError(
                "The courier's status response was not an object",
                http_status=result.http_status,
                reached_provider=True,
                correlation_id=result.correlation_id,
            )
        try:
            parsed = StatusResponse.parse(body)
        except KeyError as exc:
            raise SteadfastProtocolError(
                "The courier's status response had no delivery_status",
                http_status=result.http_status,
                reached_provider=True,
                correlation_id=result.correlation_id,
            ) from exc
        return replace(result, value=parsed)

    async def status_by_invoice(
        self, creds: SteadfastCredentials, invoice: str
    ) -> SteadfastCallResult:
        """The recovery lookup. Named separately because its use is specific."""
        return await self.get_status(creds, invoice, kind=StatusLookupKind.INVOICE)

    async def status_by_tracking_code(
        self, creds: SteadfastCredentials, tracking_code: str
    ) -> SteadfastCallResult:
        return await self.get_status(creds, tracking_code, kind=StatusLookupKind.TRACKING_CODE)

    async def get_balance(self, creds: SteadfastCredentials) -> SteadfastCallResult:
        """``GET /get_balance``. The safest documented call, so also the credential check."""
        result = await self._call(Endpoint.GET_BALANCE, creds)
        body = result.raw_body
        if not isinstance(body, dict) or "current_balance" not in body:
            raise SteadfastProtocolError(
                "The courier's balance response was not the documented shape",
                http_status=result.http_status,
                reached_provider=True,
                correlation_id=result.correlation_id,
            )
        paisa = coerce_paisa(body.get("current_balance"))
        if paisa is None:
            raise SteadfastProtocolError(
                "The courier's balance was not a readable amount",
                http_status=result.http_status,
                reached_provider=True,
                correlation_id=result.correlation_id,
            )
        return replace(result, value=paisa)

    async def create_return_request(
        self,
        creds: SteadfastCredentials,
        *,
        consignment_id: str | None = None,
        invoice: str | None = None,
        tracking_code: str | None = None,
        reason: str | None = None,
    ) -> SteadfastCallResult:
        """``POST /create_return_request``. A side effect. Sent once, never retried.

        The document says the reference is "Consignment id or user defined
        invoice id or tracking code" — one of the three. Exactly one is sent:
        sending several would leave it to the provider to decide which wins,
        and the document does not say.
        """
        provided = {
            "consignment_id": consignment_id,
            "invoice": invoice,
            "tracking_code": tracking_code,
        }
        chosen = {k: v for k, v in provided.items() if v}
        if len(chosen) != 1:
            raise ValueError(
                "A return request needs exactly one of consignment_id, invoice or tracking_code"
            )
        payload: dict[str, Any] = dict(chosen)
        if reason:
            payload["reason"] = reason

        result = await self._call(Endpoint.CREATE_RETURN_REQUEST, creds, json_body=payload)
        body = result.raw_body
        if not isinstance(body, dict):
            raise SteadfastProtocolError(
                "The courier's return response was not an object",
                http_status=result.http_status,
                reached_provider=True,
                correlation_id=result.correlation_id,
            )
        return replace(result, value=ReturnRequestRecord.parse(body))

    # ------------------------------------------------------------- path-only --
    #
    # Below here the document gives a path and a method and nothing else. These
    # are fully integrated — real calls, real parsing, real persistence — but
    # every typed field is inferred and every response is preserved whole.

    async def get_return_request(
        self, creds: SteadfastCredentials, return_id: str
    ) -> SteadfastCallResult:
        result = await self._call(Endpoint.GET_RETURN_REQUEST, creds, reference=return_id)
        body = result.raw_body
        if not isinstance(body, dict):
            raise SteadfastProtocolError(
                "The courier's return lookup was not an object",
                http_status=result.http_status,
                reached_provider=True,
                correlation_id=result.correlation_id,
            )
        return replace(result, value=ReturnRequestRecord.parse(body), schema_is_undocumented=True)

    async def list_return_requests(self, creds: SteadfastCredentials) -> SteadfastCallResult:
        result = await self._call(Endpoint.GET_RETURN_REQUESTS, creds)
        return replace(
            result,
            value=ReturnRequestRecord.parse_list(result.raw_body),
            schema_is_undocumented=True,
        )

    async def list_payments(
        self, creds: SteadfastCredentials, *, query: dict[str, Any] | None = None
    ) -> SteadfastCallResult:
        """``GET /payments``.

        ``query`` is accepted but defaults to nothing. No pagination or filter
        parameter is documented, so none is sent by default — an invented
        ``?page=2`` could silently return page one forever and make the sync
        job believe it had reached the end. The job walks pages only when the
        *response itself* declares pagination.
        """
        result = await self._call(Endpoint.PAYMENTS, creds, query=query)
        return replace(
            result,
            value=PaymentListResponse.parse(result.raw_body),
            schema_is_undocumented=True,
        )

    async def get_payment(
        self, creds: SteadfastCredentials, payment_id: str
    ) -> SteadfastCallResult:
        result = await self._call(Endpoint.PAYMENT_DETAIL, creds, reference=payment_id)
        return replace(
            result,
            value=PaymentDetailResponse.parse(result.raw_body),
            schema_is_undocumented=True,
        )

    async def list_police_stations(self, creds: SteadfastCredentials) -> SteadfastCallResult:
        result = await self._call(Endpoint.POLICE_STATIONS, creds)
        return replace(
            result,
            value=PoliceStationRecord.parse_list(result.raw_body),
            schema_is_undocumented=True,
        )

    async def aclose(self) -> None:
        await self._transport.aclose()

    # ------------------------------------------------------------- internals --

    def _headers(self, creds: SteadfastCredentials) -> dict[str, str]:
        """The three documented headers. The only place credentials are used."""
        return {
            HEADER_API_KEY: creds.api_key,
            HEADER_SECRET_KEY: creds.secret_key,
            HEADER_CONTENT_TYPE: "application/json",
            "Accept": "application/json",
        }

    async def _call(
        self,
        endpoint: Endpoint,
        creds: SteadfastCredentials,
        *,
        reference: str | None = None,
        json_body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
    ) -> SteadfastCallResult:
        method = ENDPOINT_METHODS[endpoint]
        url = self._config.url_for(endpoint, reference)
        if query:
            from urllib.parse import urlencode

            url = f"{url}?{urlencode(query)}"

        idempotent = endpoint in SAFE_TO_RETRY
        attempts = self._config.max_read_retries + 1 if idempotent else 1
        correlation_id = new_correlation_id()
        last_error: SteadfastError | None = None

        for attempt in range(attempts):
            try:
                response = await self._transport.request(
                    method,
                    url,
                    headers=self._headers(creds),
                    json_body=json_body,
                    correlation_id=correlation_id,
                    idempotent=idempotent,
                )
            except SteadfastError as exc:
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

            # A retryable server-side failure on a read.
            last_error = SteadfastUnavailableError(
                "The courier is not answering successfully",
                http_status=response.status_code,
                reached_provider=True,
                correlation_id=correlation_id,
            )
            if attempt + 1 < attempts:
                await self._sleep(self._config.retry_backoff_seconds * (attempt + 1))
                continue
            raise last_error

        raise last_error or SteadfastUnavailableError(  # pragma: no cover - loop always returns
            "The courier request did not complete", correlation_id=correlation_id
        )

    def _interpret(
        self, endpoint: Endpoint, response: RawResponse, *, idempotent: bool
    ) -> SteadfastCallResult | None:
        """Turn a raw response into a result, an exception, or ``None`` to retry.

        ``None`` is returned only for a retryable read failure. Every other
        outcome either succeeds or raises here, so the retry loop cannot
        accidentally re-send something it should not.
        """
        status = response.status_code

        if status >= 400:
            kind = classify_http(status)
            if kind is SteadfastErrorKind.UNAVAILABLE and idempotent:
                return None  # retryable
            raise self._error_for(kind, response, idempotent=idempotent)

        if response.looks_like_html:
            # A 200 carrying an HTML page is a proxy or a captive portal, not
            # Steadfast. The request reached *something*, so a write that ends
            # here is ambiguous.
            raise self._protocol_or_ambiguous(
                "The courier returned an HTML page instead of JSON",
                response,
                idempotent=idempotent,
            )

        try:
            body = json.loads(response.text) if response.text.strip() else None
        except ValueError as exc:
            raise self._protocol_or_ambiguous(
                "The courier's response was not valid JSON", response, idempotent=idempotent
            ) from exc

        if body is None:
            raise self._protocol_or_ambiguous(
                "The courier returned an empty body", response, idempotent=idempotent
            )

        # Every documented response carries its own status inside the body. A
        # 200 with a body status of anything else is a failure the HTTP layer
        # did not tell us about.
        if isinstance(body, dict) and "status" in body:
            body_status = body.get("status")
            if isinstance(body_status, int) and body_status != BODY_STATUS_OK:
                kind = classify_http(body_status)
                raise self._error_for(
                    kind, response, idempotent=idempotent, body_status=body_status
                )

        return SteadfastCallResult(
            value=None,
            correlation_id=response.correlation_id,
            http_status=status,
            raw_body=body,
            endpoint=str(endpoint),
            elapsed_ms=response.elapsed_ms,
        )

    def _error_for(
        self,
        kind: SteadfastErrorKind,
        response: RawResponse,
        *,
        idempotent: bool,
        body_status: int | None = None,
    ) -> SteadfastError:
        context: dict[str, Any] = {"endpoint_status": response.status_code}
        if body_status is not None:
            context["body_status"] = body_status

        message = _MESSAGE_FOR_KIND[kind]
        if kind is SteadfastErrorKind.AUTH:
            return SteadfastAuthError(
                message,
                http_status=response.status_code,
                reached_provider=True,
                correlation_id=response.correlation_id,
                technical_context=context,
            )
        if kind is SteadfastErrorKind.RATE_LIMITED:
            return SteadfastRateLimitedError(
                message,
                http_status=response.status_code,
                reached_provider=True,
                correlation_id=response.correlation_id,
                technical_context=context,
            )
        if kind is SteadfastErrorKind.VALIDATION:
            return SteadfastValidationError(
                message,
                http_status=response.status_code,
                reached_provider=True,
                correlation_id=response.correlation_id,
                technical_context=context,
            )
        if kind is SteadfastErrorKind.UNAVAILABLE:
            # Reached here only for a non-idempotent call: the provider errored
            # *after* receiving a write, so the write may have been applied.
            if not idempotent:
                return SteadfastAmbiguousError(
                    "The courier failed after receiving the request",
                    http_status=response.status_code,
                    correlation_id=response.correlation_id,
                    technical_context=context,
                )
            return SteadfastUnavailableError(
                message,
                http_status=response.status_code,
                reached_provider=True,
                correlation_id=response.correlation_id,
                technical_context=context,
            )
        return SteadfastError(
            message,
            kind=kind,
            http_status=response.status_code,
            reached_provider=True,
            correlation_id=response.correlation_id,
            technical_context=context,
        )

    @staticmethod
    def _protocol_or_ambiguous(
        message: str, response: RawResponse, *, idempotent: bool
    ) -> SteadfastError:
        if idempotent:
            return SteadfastProtocolError(
                message,
                http_status=response.status_code,
                reached_provider=True,
                correlation_id=response.correlation_id,
            )
        return SteadfastAmbiguousError(
            message,
            http_status=response.status_code,
            correlation_id=response.correlation_id,
            technical_context={"protocol": "unreadable response to a write"},
        )


_MESSAGE_FOR_KIND: dict[SteadfastErrorKind, str] = {
    SteadfastErrorKind.AUTH: "The courier rejected these credentials",
    SteadfastErrorKind.VALIDATION: "The courier rejected the request",
    SteadfastErrorKind.ADDRESS_REJECTED: "The courier rejected the address",
    SteadfastErrorKind.RATE_LIMITED: "The courier is rate limiting this account",
    SteadfastErrorKind.UNAVAILABLE: "The courier is not available",
    SteadfastErrorKind.PROTOCOL: "The courier returned an unexpected response",
    SteadfastErrorKind.AMBIGUOUS: "The courier's answer was lost",
    SteadfastErrorKind.UNKNOWN: "The courier request failed",
}
