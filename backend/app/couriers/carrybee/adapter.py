"""Carrybee behind the standard courier interface (V4 WP7.2).

Built from ``docs/providers/carrybee/CONTRACT.md`` only. What it does:

*   **Credentials** are checked with ``GET /api/v2/stores`` — an authenticated
    read that changes nothing. The store list is also the pickup-store picker:
    ``store_id`` is required on every create, so an account books only after a
    store is chosen.
*   **Create** is ``POST /api/v2/orders``, sent once: Normal delivery, Parcel,
    whole-Taka COD (the paisa remainder is recorded, not dropped), the free-text
    address (city / zone / area are optional on the page and never guessed).
    A timeout, a 5xx or an unreadable answer is ``UNKNOWN`` — a parcel may
    exist — and is never re-sent (the details lookup accepts the merchant order
    id; automatic recovery is Steadfast-only, so a person resolves it).
*   **Status** is ``GET /api/v2/orders/{id}/details``. ``transfer_status`` has
    no published values, so it is kept verbatim and claims no state unless it
    is one of the documented event names.
*   **Cancel** is ``POST /api/v2/orders/{id}/cancel``.

Bulk create (asynchronous, results only by webhook), reverse pickup, exchange
and location lookups are documented but not used; quote, balance, payouts and
customer stats are not in the page.
"""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.common.money import Money
from app.common.phone import try_normalize_bd_phone
from app.core.logging import get_logger
from app.core.redaction import mask_phone
from app.couriers.adapter import (
    BookingOutcome,
    BookingPreview,
    BookingRequest,
    BookingResult,
    ProviderConsignment,
    ProviderCustomerStats,
    ProviderEvent,
    ProviderPayout,
    ProviderStatus,
    Quote,
    Store,
    Unavailable,
    ValidationResult,
)
from app.couriers.capabilities import Capability
from app.couriers.carrybee.client import CARRYBEE_PROVIDER, CarrybeeClient, CarrybeeCredentials
from app.couriers.carrybee.contract import (
    COLLECTABLE_TAKA_RANGE,
    DELIVERY_TYPE_NORMAL,
    PRODUCT_TYPE_PARCEL,
    QUANTITY_RANGE,
    TEXT_LIMITS,
    WEIGHT_GRAMS_RANGE,
)
from app.couriers.carrybee.mapping import map_carrybee_status
from app.couriers.carrybee.webhooks import parse_carrybee_events
from app.couriers.http import ProviderError, ProviderErrorKind, ProviderProtocolError

__all__ = [
    "DEFAULT_WEIGHT_GRAMS",
    "PROVIDER",
    "SUPPORTED_CAPABILITIES",
    "CarrybeeAdapter",
    "build_create_payload",
]

log = get_logger(__name__)

PROVIDER = CARRYBEE_PROVIDER

SUPPORTED_CAPABILITIES: frozenset[Capability] = frozenset(
    {
        Capability.CREDENTIAL_VALIDATION,
        Capability.CREATE_SINGLE,
        Capability.STATUS_LOOKUP,
        Capability.CANCEL,
        Capability.LIST_STORES,
        Capability.WEBHOOK,
    }
)

_UNSUPPORTED_REASONS: dict[Capability, str] = {
    Capability.CREATE_BULK: (
        "Carrybee's bulk booking reports results only by webhook, so ecomsbd books "
        "Carrybee parcels one at a time."
    ),
    Capability.RETURNS: "Start a Carrybee return (reverse pickup) from your Carrybee panel.",
    Capability.BALANCE: "Carrybee's API has no account-balance call.",
    Capability.PAYOUTS: "Carrybee's API has no payout statement. Upload your statement instead.",
    Capability.CUSTOMER_STATS: "Carrybee's API has no customer delivery-history call.",
    Capability.PRICE_QUOTE: "Carrybee's API has no charge-quote call.",
}

#: ``item_weight`` is required (CONTRACT.md §6.1). When an order carries no
#: weight this is ecomsbd's policy, the same as for RedX — not a Carrybee rule.
DEFAULT_WEIGHT_GRAMS = 500
#: Sent with a cancel; ``cancellation_reason`` is required (CONTRACT.md §6.4).
CANCEL_REASON = "Cancelled by the merchant in ecomsbd"


def _whole_taka(cod: Money) -> tuple[int, int]:
    taka = int((Decimal(cod.paisa) / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return taka, cod.paisa - taka * 100


def _text(value: str | None, name: str, *, required: bool, truncate: bool) -> str | None:
    low, high = TEXT_LIMITS[name]
    text = " ".join((value or "").split())
    if not text:
        if required:
            raise ValueError(f"Carrybee needs {name}")
        return None
    if len(text) > high:
        if not truncate:
            raise ValueError(f"Carrybee allows at most {high} characters in {name}")
        text = text[:high].rstrip()
    if len(text) < low:
        raise ValueError(f"Carrybee needs at least {low} characters in {name}")
    return text


def _in_range(value: int, bounds: tuple[int, int], name: str) -> int:
    low, high = bounds
    if not low <= value <= high:
        raise ValueError(f"Carrybee accepts {name} from {low} to {high}")
    return value


def _phone(e164: str) -> str:
    number = try_normalize_bd_phone(e164)
    if number is None:
        raise ValueError("Not a valid Bangladeshi mobile number for Carrybee")
    return number.national


def build_create_payload(req: BookingRequest, merchant_reference: str) -> dict[str, Any]:
    """The ``POST /api/v2/orders`` body. Raises ``ValueError`` for a booking
    Carrybee would refuse, before anything is sent."""
    if not req.store_reference:
        raise ValueError("Choose a Carrybee pickup store for this account first")
    cod_taka, _residual = _whole_taka(req.cod_amount)
    payload: dict[str, Any] = {
        "store_id": req.store_reference,
        "merchant_order_id": _text(
            merchant_reference, "merchant_order_id", required=True, truncate=False
        ),
        "delivery_type": DELIVERY_TYPE_NORMAL,
        "product_type": PRODUCT_TYPE_PARCEL,
        "recipient_phone": _phone(req.recipient_phone_e164),
        "recipient_name": _text(req.recipient_name, "recipient_name", required=True, truncate=True),
        "recipient_address": _text(
            req.recipient_address, "recipient_address", required=True, truncate=False
        ),
        "item_weight": _in_range(
            req.weight_grams or DEFAULT_WEIGHT_GRAMS, WEIGHT_GRAMS_RANGE, "item_weight"
        ),
        "item_quantity": _in_range(max(req.item_quantity, 1), QUANTITY_RANGE, "item_quantity"),
        "collectable_amount": _in_range(cod_taka, COLLECTABLE_TAKA_RANGE, "collectable_amount"),
    }
    if req.recipient_alternate_phone_e164:
        # The page spells the field this way (CONTRACT.md §6.1).
        payload["recipient_secendary_phone"] = _phone(req.recipient_alternate_phone_e164)
    for name, value in (
        ("special_instruction", req.note),
        ("product_description", req.item_description),
    ):
        text = _text(value, name, required=False, truncate=True)
        if text:
            payload[name] = text
    return payload


def _mask_payload(payload: dict[str, Any]) -> dict[str, Any]:
    masked = dict(payload)
    for key in ("recipient_phone", "recipient_secendary_phone"):
        if key in masked:
            masked[key] = mask_phone(str(masked[key]))
    return masked


class CarrybeeAdapter:
    provider = PROVIDER

    def __init__(self, client: CarrybeeClient) -> None:
        self._client = client

    @property
    def client(self) -> CarrybeeClient:
        return self._client

    @property
    def bulk_chunk_size(self) -> int:
        return 1

    def describe_booking(self, req: BookingRequest, merchant_reference: str) -> BookingPreview:
        payload = build_create_payload(req, merchant_reference)
        cod_taka, residual = _whole_taka(req.cod_amount)
        return BookingPreview(
            redacted_payload=_mask_payload(payload),
            recipient_phone_masked=mask_phone(str(payload["recipient_phone"])) or "",
            cod_taka=cod_taka,
            cod_residual_paisa=residual,
        )

    async def capabilities(self, creds: Any) -> set[Capability]:
        return set(SUPPORTED_CAPABILITIES)

    async def validate_credentials(self, creds: Any) -> ValidationResult:
        credentials = _as_credentials(creds)
        try:
            result = await self._client.list_stores(credentials)
        except ProviderError as exc:
            if exc.is_auth_failure:
                return ValidationResult(
                    valid=False,
                    rejected=True,
                    message=(
                        "Carrybee rejected these credentials. Check the "
                        + ("Sandbox" if credentials.sandbox else "Production")
                        + " Client-ID, Client-Secret and Client-Context on Carrybee's "
                        "API Credentials page."
                    ),
                    account_label=credentials.masked_identifier,
                )
            return ValidationResult(
                valid=False,
                rejected=False,
                message=(
                    "Carrybee did not answer, so these credentials could not be checked. "
                    "They have not been marked wrong — try again shortly."
                ),
                account_label=credentials.masked_identifier,
            )
        log.info(
            "carrybee credentials validated",
            extra={"provider": PROVIDER, "correlation_id": result.correlation_id},
        )
        return ValidationResult(
            valid=True,
            message="Connected to Carrybee.",
            detected_capabilities=frozenset(SUPPORTED_CAPABILITIES),
            account_label=credentials.masked_identifier,
        )

    async def list_stores(self, creds: Any) -> list[Store] | Unavailable:
        result = await self._client.list_stores(_as_credentials(creds))
        data = result.data
        rows = data.get("stores") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise ProviderProtocolError(
                "Carrybee's store list could not be read",
                provider=PROVIDER,
                http_status=result.http_status,
                reached_provider=True,
                correlation_id=result.correlation_id,
            )
        stores: list[Store] = []
        for row in rows:
            if not isinstance(row, dict) or row.get("id") in (None, ""):
                continue
            stores.append(
                Store(
                    provider_store_id=str(row["id"]),
                    name=str(row.get("name") or row["id"]),
                    address=str(row["address"]) if row.get("address") else None,
                    raw={
                        key: row.get(key)
                        for key in (
                            "is_active",
                            "is_approved",
                            "is_default_pickup_store",
                            "city_id",
                            "zone_id",
                            "area_id",
                        )
                    },
                )
            )
        return stores

    async def quote(self, creds: Any, req: BookingRequest) -> Quote | Unavailable:
        return Unavailable(Capability.PRICE_QUOTE, _UNSUPPORTED_REASONS[Capability.PRICE_QUOTE])

    async def create_consignment(
        self, creds: Any, req: BookingRequest, merchant_reference: str
    ) -> BookingResult:
        """``BOOKED`` with a consignment id, ``FAILED`` on a clear refusal,
        ``UNKNOWN`` otherwise. Never retried."""
        credentials = _as_credentials(creds)
        try:
            payload = build_create_payload(req, merchant_reference)
        except ValueError as exc:
            return BookingResult(
                outcome=BookingOutcome.FAILED,
                error_code=str(ProviderErrorKind.VALIDATION),
                error_message=str(exc),
            )
        try:
            result = await self._client.create_order(credentials, payload)
        except ProviderError as exc:
            outcome = (
                BookingOutcome.UNKNOWN if exc.create_may_have_succeeded else BookingOutcome.FAILED
            )
            return BookingResult(
                outcome=outcome,
                error_code=str(exc.kind),
                error_message=str(exc),
                provider_request_id=exc.correlation_id,
            )
        data = result.data
        order = data.get("order") if isinstance(data, dict) else None
        consignment_id = order.get("consignment_id") if isinstance(order, dict) else None
        if not isinstance(consignment_id, str) or not consignment_id.strip():
            # Carrybee said "created" but named no parcel: it may exist.
            return BookingResult(
                outcome=BookingOutcome.UNKNOWN,
                error_code=str(ProviderErrorKind.AMBIGUOUS),
                error_message="Carrybee accepted the order but returned no consignment id.",
                provider_request_id=result.correlation_id,
            )
        assert isinstance(order, dict)  # noqa: S101 - narrowed above
        return BookingResult(
            outcome=BookingOutcome.BOOKED,
            consignment=ProviderConsignment(
                provider_consignment_id=consignment_id.strip(),
                tracking_code=consignment_id.strip(),
                merchant_reference=merchant_reference,
                raw_status=None,
                # Fees come back as decimal strings in Taka; a money decision,
                # not converted here.
                charge=None,
                raw=dict(order),
            ),
            provider_request_id=result.correlation_id,
        )

    async def create_bulk(
        self, creds: Any, reqs: list[BookingRequest]
    ) -> list[BookingResult] | Unavailable:
        """Refused per item, nothing sent (see ``bulk_chunk_size``)."""
        return [
            BookingResult(
                outcome=BookingOutcome.FAILED,
                error_code=str(ProviderErrorKind.VALIDATION),
                error_message=_UNSUPPORTED_REASONS[Capability.CREATE_BULK],
            )
            for _ in reqs
        ]

    async def get_status(self, creds: Any, reference: str) -> ProviderStatus | Unavailable:
        """By consignment id or merchant order id (CONTRACT.md §6.3)."""
        result = await self._client.order_details(_as_credentials(creds), reference)
        data = result.data
        raw_status = data.get("transfer_status") if isinstance(data, dict) else None
        if not isinstance(data, dict) or not isinstance(raw_status, str) or not raw_status:
            raise ProviderProtocolError(
                "Carrybee returned the order without a status",
                provider=PROVIDER,
                http_status=result.http_status,
                reached_provider=True,
                correlation_id=result.correlation_id,
            )
        mapping = map_carrybee_status(raw_status)
        updated_at = None
        if isinstance(data.get("updated_at"), str):
            try:
                updated_at = datetime.fromisoformat(data["updated_at"])
            except ValueError:
                updated_at = None
        raw = {key: value for key, value in data.items() if not key.startswith("recipient_")}
        return ProviderStatus(
            provider_consignment_id=str(data.get("consignment_id") or reference),
            raw_status=raw_status,
            normalized_status=str(mapping.canonical) if mapping.canonical else None,
            updated_at=updated_at,
            raw=raw,
        )

    async def cancel(self, creds: Any, reference: str) -> bool | Unavailable:
        """``True`` when Carrybee confirms. A lost answer raises (ambiguous)."""
        await self._client.cancel_order(_as_credentials(creds), reference, CANCEL_REASON)
        return True

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        """Always ``False`` here: the secret is per shop, checked by the route
        with :class:`~app.couriers.carrybee.webhooks.CarrybeeWebhookVerifier`."""
        return False

    def parse_webhook(self, headers: dict[str, str], body: bytes) -> list[ProviderEvent]:
        events: list[ProviderEvent] = []
        for event in parse_carrybee_events(body):
            canonical = map_carrybee_status(event.raw_status).canonical
            events.append(
                ProviderEvent(
                    provider_consignment_id=event.provider_consignment_id or "",
                    raw_status=event.raw_status or "",
                    occurred_at=event.occurred_at,
                    provider_event_id=None,
                    normalized_status=str(canonical) if canonical else None,
                    raw=event.raw,
                )
            )
        return events

    async def request_return(self, creds: Any, reference: str, reason: str) -> bool | Unavailable:
        return Unavailable(Capability.RETURNS, _UNSUPPORTED_REASONS[Capability.RETURNS])

    async def get_balance(self, creds: Any) -> Money | Unavailable:
        return Unavailable(Capability.BALANCE, _UNSUPPORTED_REASONS[Capability.BALANCE])

    async def list_payouts(self, creds: Any, since: datetime) -> list[ProviderPayout] | Unavailable:
        return Unavailable(Capability.PAYOUTS, _UNSUPPORTED_REASONS[Capability.PAYOUTS])

    async def customer_stats(
        self, creds: Any, phone_e164: str
    ) -> ProviderCustomerStats | Unavailable:
        return Unavailable(
            Capability.CUSTOMER_STATS, _UNSUPPORTED_REASONS[Capability.CUSTOMER_STATS]
        )


def _as_credentials(creds: Any) -> CarrybeeCredentials:
    if isinstance(creds, CarrybeeCredentials):
        return creds
    if isinstance(creds, dict):
        return CarrybeeCredentials(
            client_id=str(creds["client_id"]),
            client_secret=str(creds["client_secret"]),
            client_context=str(creds["client_context"]),
            sandbox=bool(creds.get("sandbox", False)),
        )
    raise TypeError("Carrybee needs CarrybeeCredentials")
