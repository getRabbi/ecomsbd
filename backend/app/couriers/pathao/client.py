"""Typed access to the Pathao merchant surface ecomsbd has verified.

The one thing this client does that the Steadfast client does not is hold a
token. Pathao authenticates with a short-lived bearer token issued from
``client_id``/``client_secret``, so there is a cache, and a cache next to money
is worth being explicit about:

*   **Tokens are keyed by credential, not by provider.** Two shops on one
    process must never share one. The key is a salted digest of the
    credentials, so the cache itself holds no credential.
*   **Expiry is applied with a margin.** A token that expires mid-flight turns a
    create into a 401 and, worse, a create whose outcome we then have to reason
    about. Refreshing early costs one cheap call.
*   **A create never triggers a token refresh mid-request.** The token is
    ensured *before* the create is sent. A 401 on a create is treated as a
    clean, deterministic auth failure — the provider answered and refused, so
    nothing was created — and is never re-sent with a new token.

Everything about retries follows the house rule: reads may be retried, a create
never is, whatever the configuration says.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.core.clock import utc_now
from app.core.logging import get_logger
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
from app.couriers.pathao.contract import (
    ENDPOINT_METHODS,
    HEADER_ACCEPT,
    HEADER_AUTHORIZATION,
    HEADER_CONTENT_TYPE,
    HEADER_SOURCE,
    LIVE_BASE_URL,
    SAFE_TO_RETRY,
    SANDBOX_BASE_URL,
    SOURCE_VALUE,
    Endpoint,
)
from app.couriers.pathao.dto import (
    CreateOrderResponse,
    TokenResponse,
    parse_area_list,
    parse_city_list,
    parse_create_order,
    parse_stores,
    parse_token,
    parse_zone_list,
)

__all__ = [
    "PATHAO_PROVIDER",
    "PathaoCallResult",
    "PathaoClient",
    "PathaoConfig",
    "PathaoCredentials",
]

log = get_logger(__name__)

PATHAO_PROVIDER = "pathao"

#: Refresh this many seconds before Pathao says the token expires.
TOKEN_EXPIRY_MARGIN_SECONDS = 120

#: Used when Pathao returns no ``expires_in``. Short on purpose: a wrong guess
#: that is too short costs one extra login, a wrong guess that is too long
#: costs a failed booking.
TOKEN_FALLBACK_TTL_SECONDS = 1800


@dataclass(frozen=True, slots=True)
class PathaoCredentials:
    """One merchant's Pathao API client credentials.

    ``__repr__`` is overridden and ``__str__`` follows it, so a credential
    cannot reach a log line or a traceback by being interpolated into a
    message — which is how they usually get there.
    """

    client_id: str
    client_secret: str
    #: Chosen at connect time and stored with the account. ``True`` routes every
    #: call to Pathao's sandbox host.
    sandbox: bool = False

    def __repr__(self) -> str:
        return "PathaoCredentials(client_id='[redacted]', client_secret='[redacted]')"

    __str__ = __repr__

    @property
    def masked_identifier(self) -> str:
        """A stable, non-reversible hint the seller can recognise."""
        if len(self.client_id) <= 4:
            return "****"
        return f"****{self.client_id[-4:]}"

    @property
    def cache_key(self) -> str:
        """A digest that identifies these credentials without containing them."""
        material = f"{self.client_id}\x00{self.client_secret}\x00{int(self.sandbox)}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class PathaoConfig:
    """Client tuning. Nothing here is a claim about Pathao.

    Pathao publishes no rate limit, no retry contract and no batch ceiling, so
    every value is ecomsbd's own conservative policy and is configurable.
    """

    live_base_url: str = LIVE_BASE_URL
    sandbox_base_url: str = SANDBOX_BASE_URL

    #: Deliberately small. A failed 50-item request is 50 parcels in an unknown
    #: state; Pathao permitting more would not be a reason to send more.
    bulk_chunk_size: int = 50

    #: Reads only. A create is never retried, whatever this says.
    max_read_retries: int = 2
    retry_backoff_seconds: float = 0.5

    def __post_init__(self) -> None:
        if not 1 <= self.bulk_chunk_size <= 200:
            raise ValueError("bulk_chunk_size must be between 1 and 200")

    def base_url(self, *, sandbox: bool) -> str:
        return (self.sandbox_base_url if sandbox else self.live_base_url).rstrip("/")

    def url_for(self, endpoint: Endpoint, *, sandbox: bool, reference: str | None = None) -> str:
        path = endpoint.with_reference(reference) if reference is not None else str(endpoint)
        return f"{self.base_url(sandbox=sandbox)}{path}"


@dataclass(frozen=True, slots=True)
class PathaoCallResult:
    """A parsed response plus the evidence trail behind it."""

    value: Any
    correlation_id: str
    http_status: int
    raw_body: Any
    endpoint: str
    elapsed_ms: int = 0
    #: Set when the endpoint's envelope shape is inferred rather than published,
    #: so the caller can record that its typed fields are not guaranteed.
    schema_is_inferred: bool = False


@dataclass(slots=True)
class _CachedToken:
    access_token: str
    expires_at_epoch: float

    def is_fresh(self, *, now: float) -> bool:
        return now + TOKEN_EXPIRY_MARGIN_SECONDS < self.expires_at_epoch


class PathaoClient:
    """Pathao's published merchant surface, typed."""

    provider = PATHAO_PROVIDER

    def __init__(
        self,
        transport: ProviderTransport,
        *,
        config: PathaoConfig | None = None,
        sleep: Callable[[float], Any] | None = None,
    ) -> None:
        self._transport = transport
        self._config = config or PathaoConfig()
        self._sleep = sleep or asyncio.sleep
        self._tokens: dict[str, _CachedToken] = {}
        self._token_locks: dict[str, asyncio.Lock] = {}

    @property
    def config(self) -> PathaoConfig:
        return self._config

    # --------------------------------------------------------------- auth --

    async def login(self, creds: PathaoCredentials) -> PathaoCallResult:
        """Exchange client credentials for an access token.

        The only call that carries the client secret, and the only one that
        sends no bearer token.
        """
        result = await self._raw_call(
            Endpoint.LOGIN,
            creds,
            json_body={"client_id": creds.client_id, "client_secret": creds.client_secret},
            authenticated=False,
        )
        token = parse_token(result.raw_body)
        if token is None:
            # A 2xx with no token is not a credential verdict; it is a response
            # we cannot read. Treated as a protocol failure so it never counts
            # against the seller's credentials.
            raise ProviderProtocolError(
                "Pathao's login response contained no access token",
                provider=PATHAO_PROVIDER,
                http_status=result.http_status,
                reached_provider=True,
                correlation_id=result.correlation_id,
            )
        return _with_value(result, token)

    async def ensure_token(self, creds: PathaoCredentials) -> str:
        """A usable access token, from cache or by logging in.

        Serialised per credential: a burst of bookings for one shop must
        produce one login, not one per parcel.
        """
        key = creds.cache_key
        now = utc_now().timestamp()

        cached = self._tokens.get(key)
        if cached is not None and cached.is_fresh(now=now):
            return cached.access_token

        lock = self._token_locks.setdefault(key, asyncio.Lock())
        async with lock:
            # Re-check: another task may have refreshed while we waited.
            cached = self._tokens.get(key)
            now = utc_now().timestamp()
            if cached is not None and cached.is_fresh(now=now):
                return cached.access_token

            result = await self.login(creds)
            token: TokenResponse = result.value
            ttl = token.expires_in if token.expires_in and token.expires_in > 0 else None
            self._tokens[key] = _CachedToken(
                access_token=token.access_token,
                expires_at_epoch=now + (ttl or TOKEN_FALLBACK_TTL_SECONDS),
            )
            log.info(
                "pathao token issued",
                extra={
                    "provider": PATHAO_PROVIDER,
                    "operation": "login",
                    "correlation_id": result.correlation_id,
                    # The lifetime, never the token.
                    "expires_in_seconds": ttl,
                },
            )
            return token.access_token

    def forget_token(self, creds: PathaoCredentials) -> None:
        """Drop a cached token, after the provider has rejected it."""
        self._tokens.pop(creds.cache_key, None)

    # -------------------------------------------------------------- reads --

    async def user_short_info(self, creds: PathaoCredentials) -> PathaoCallResult:
        """The safest published read. Used as the credential check."""
        return await self._call(Endpoint.USER_SHORT_INFO, creds)

    async def list_stores(self, creds: PathaoCredentials) -> PathaoCallResult:
        result = await self._call(Endpoint.STORES, creds)
        return _with_value(result, parse_stores(result.raw_body), schema_is_inferred=True)

    async def list_cities(self, creds: PathaoCredentials) -> PathaoCallResult:
        result = await self._call(Endpoint.CITY_LIST, creds)
        return _with_value(result, parse_city_list(result.raw_body), schema_is_inferred=True)

    async def list_zones(self, creds: PathaoCredentials, city_id: int) -> PathaoCallResult:
        result = await self._call(Endpoint.ZONE_LIST, creds, reference=str(city_id))
        return _with_value(result, parse_zone_list(result.raw_body), schema_is_inferred=True)

    async def list_areas(self, creds: PathaoCredentials, zone_id: int) -> PathaoCallResult:
        result = await self._call(Endpoint.AREA_LIST, creds, reference=str(zone_id))
        return _with_value(result, parse_area_list(result.raw_body), schema_is_inferred=True)

    # ------------------------------------------------------------ creates --

    async def create_order(
        self, creds: PathaoCredentials, payload: dict[str, Any]
    ) -> PathaoCallResult:
        """Create one parcel. Sent exactly once, never retried."""
        result = await self._call(Endpoint.CREATE_ORDER, creds, json_body=payload)
        parsed: CreateOrderResponse | None = parse_create_order(result.raw_body)
        if parsed is None:
            # Pathao accepted the request and told us nothing we can act on.
            # The parcel may exist, so this is the ambiguous case, not a
            # failure — and it must never be re-sent.
            raise ProviderAmbiguousError(
                "Pathao accepted the booking but returned no consignment id",
                provider=PATHAO_PROVIDER,
                http_status=result.http_status,
                correlation_id=result.correlation_id,
            )
        return _with_value(result, parsed)

    async def create_order_bulk(
        self, creds: PathaoCredentials, payloads: list[dict[str, Any]]
    ) -> PathaoCallResult:
        """Create a chunk of parcels in one request.

        Chunking across requests is the service's job, because only it can
        persist a booking attempt before each chunk goes out. This method sends
        exactly one request and never more.
        """
        return await self._call(Endpoint.CREATE_ORDER_BULK, creds, json_body={"orders": payloads})

    async def aclose(self) -> None:
        await self._transport.aclose()

    # ---------------------------------------------------------- internals --

    async def _call(
        self,
        endpoint: Endpoint,
        creds: PathaoCredentials,
        *,
        reference: str | None = None,
        json_body: Any | None = None,
    ) -> PathaoCallResult:
        """An authenticated call, with the token ensured before it is sent."""
        # Ensured *before* the request, so a create never has to refresh
        # mid-flight and never has to be re-sent after a token expiry.
        token = await self.ensure_token(creds)
        return await self._raw_call(
            endpoint,
            creds,
            reference=reference,
            json_body=json_body,
            authenticated=True,
            token=token,
        )

    async def _raw_call(
        self,
        endpoint: Endpoint,
        creds: PathaoCredentials,
        *,
        reference: str | None = None,
        json_body: Any | None = None,
        authenticated: bool,
        token: str | None = None,
    ) -> PathaoCallResult:
        method = ENDPOINT_METHODS[endpoint]
        url = self._config.url_for(endpoint, sandbox=creds.sandbox, reference=reference)

        idempotent = endpoint in SAFE_TO_RETRY
        attempts = self._config.max_read_retries + 1 if idempotent else 1
        correlation_id = new_correlation_id()
        last_error: ProviderError | None = None

        for attempt in range(attempts):
            try:
                response = await self._transport.request(
                    method,
                    url,
                    headers=self._headers(authenticated=authenticated, token=token),
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

            parsed = self._interpret(endpoint, response, creds, idempotent=idempotent)
            if parsed is not None:
                return parsed

            last_error = ProviderUnavailableError(
                "Pathao is not answering successfully",
                provider=PATHAO_PROVIDER,
                http_status=response.status_code,
                reached_provider=True,
                correlation_id=correlation_id,
            )
            if attempt + 1 < attempts:
                await self._sleep(self._config.retry_backoff_seconds * (attempt + 1))
                continue
            raise last_error

        raise last_error or ProviderUnavailableError(  # pragma: no cover - loop returns
            "The Pathao request did not complete",
            provider=PATHAO_PROVIDER,
            correlation_id=correlation_id,
        )

    def _headers(self, *, authenticated: bool, token: str | None) -> dict[str, str]:
        headers = {
            HEADER_CONTENT_TYPE: "application/json",
            HEADER_ACCEPT: "application/json",
            HEADER_SOURCE: SOURCE_VALUE,
        }
        if authenticated and token:
            headers[HEADER_AUTHORIZATION] = f"Bearer {token}"
        return headers

    def _interpret(
        self,
        endpoint: Endpoint,
        response: RawResponse,
        creds: PathaoCredentials,
        *,
        idempotent: bool,
    ) -> PathaoCallResult | None:
        """Turn a raw response into a result, an exception, or ``None`` to retry.

        ``None`` is returned only for a retryable read failure. Every other
        outcome either succeeds or raises here, so the retry loop cannot
        accidentally re-send something it should not.
        """
        status = response.status_code

        if status >= 400:
            kind = classify_http(status)
            if kind is ProviderErrorKind.AUTH:
                # The cached token is no longer usable, whatever the cause.
                # Dropping it means the next *read* logs in again cleanly; it
                # does not cause this call to be re-sent.
                self.forget_token(creds)
            if kind is ProviderErrorKind.UNAVAILABLE and idempotent:
                return None  # retryable
            raise self._error_for(kind, response, idempotent=idempotent)

        if response.looks_like_html:
            # A 2xx carrying an HTML page is a proxy or a captive portal, not
            # Pathao. The request reached *something*, so a write that ends
            # here is ambiguous.
            raise self._protocol_or_ambiguous(
                "Pathao returned an HTML page instead of JSON",
                response,
                idempotent=idempotent,
            )

        try:
            body = json.loads(response.text) if response.text.strip() else None
        except ValueError as exc:
            raise self._protocol_or_ambiguous(
                "Pathao's response was not valid JSON", response, idempotent=idempotent
            ) from exc

        if body is None:
            raise self._protocol_or_ambiguous(
                "Pathao returned an empty body", response, idempotent=idempotent
            )

        return PathaoCallResult(
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

        if kind is ProviderErrorKind.AUTH:
            return ProviderAuthError(
                message,
                provider=PATHAO_PROVIDER,
                http_status=response.status_code,
                reached_provider=True,
                correlation_id=response.correlation_id,
                technical_context=context,
            )
        if kind is ProviderErrorKind.RATE_LIMITED:
            return ProviderRateLimitedError(
                message,
                provider=PATHAO_PROVIDER,
                http_status=response.status_code,
                reached_provider=True,
                correlation_id=response.correlation_id,
                technical_context=context,
            )
        if kind is ProviderErrorKind.UNAVAILABLE:
            # Pathao answered with a 5xx, so it certainly received the request.
            # For a write that makes the outcome ambiguous, not failed.
            if not idempotent:
                return ProviderAmbiguousError(
                    "Pathao failed while handling the booking",
                    provider=PATHAO_PROVIDER,
                    http_status=response.status_code,
                    correlation_id=response.correlation_id,
                    technical_context=context,
                )
            return ProviderUnavailableError(
                message,
                provider=PATHAO_PROVIDER,
                http_status=response.status_code,
                reached_provider=True,
                correlation_id=response.correlation_id,
                technical_context=context,
            )

        return ProviderError(
            message,
            provider=PATHAO_PROVIDER,
            kind=kind,
            http_status=response.status_code,
            reached_provider=True,
            correlation_id=response.correlation_id,
            technical_context=context,
        )

    def _protocol_or_ambiguous(
        self, message: str, response: RawResponse, *, idempotent: bool
    ) -> ProviderError:
        if idempotent:
            return ProviderProtocolError(
                message,
                provider=PATHAO_PROVIDER,
                http_status=response.status_code,
                reached_provider=True,
                correlation_id=response.correlation_id,
            )
        return ProviderAmbiguousError(
            message,
            provider=PATHAO_PROVIDER,
            http_status=response.status_code,
            correlation_id=response.correlation_id,
        )


_MESSAGE_FOR_KIND: dict[ProviderErrorKind, str] = {
    ProviderErrorKind.AUTH: "Pathao rejected these API credentials.",
    ProviderErrorKind.VALIDATION: "Pathao rejected the request.",
    ProviderErrorKind.ADDRESS_REJECTED: "Pathao could not accept that address.",
    ProviderErrorKind.RATE_LIMITED: "Pathao is rate limiting this account.",
    ProviderErrorKind.UNAVAILABLE: "Pathao is not available right now.",
    ProviderErrorKind.PROTOCOL: "Pathao's response could not be read.",
    ProviderErrorKind.AMBIGUOUS: "The outcome of the Pathao request is unknown.",
    ProviderErrorKind.UNKNOWN: "The Pathao request failed.",
}


def _with_value(
    result: PathaoCallResult, value: Any, *, schema_is_inferred: bool = False
) -> PathaoCallResult:
    return PathaoCallResult(
        value=value,
        correlation_id=result.correlation_id,
        http_status=result.http_status,
        raw_body=result.raw_body,
        endpoint=result.endpoint,
        elapsed_ms=result.elapsed_ms,
        schema_is_inferred=schema_is_inferred or result.schema_is_inferred,
    )
