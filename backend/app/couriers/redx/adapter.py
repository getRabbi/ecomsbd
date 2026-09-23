"""The RedX courier adapter.

Implements :class:`~app.couriers.adapter.CourierAdapter` against RedX's own
developer documentation (:mod:`app.couriers.redx.contract`). Everything
RedX-specific stops here: field names, the phone shape, the delivery-area
requirement, units and the status vocabulary. Order, money and profit code sees
only the normalized types and never branches on a provider name.

The boundary transforms live here for the same reason they do in the other
adapters — they are where a unit or a shape changes:

*   **Phone.** ecomsbd stores ``+8801XXXXXXXXX``; RedX's examples use the
    11-digit national number.
*   **Money.** ecomsbd stores paisa; ``cash_collection_amount`` is taka, sent as
    the string the documentation types it as. A courier collects banknotes, so
    whole taka are asked for and the paisa remainder is returned, not dropped.
*   **Weight.** Grams, the unit RedX's parcel details and charge calculator
    both state.
*   **Delivery area.** RedX requires ``delivery_area`` *and*
    ``delivery_area_id`` on every create and says nothing about resolving a
    free-text address. The seller picks the area from RedX's own list; a
    booking without one is refused before anything is sent. Guessing an area
    from an address would be a parcel routed to the wrong hub.

The capability split is the documented one. RedX's Open API creates, looks up,
tracks and cancels parcels, lists areas and pickup stores, quotes a charge and
sends status callbacks. It has no bulk create, no return request, no balance,
no payouts and no customer history, so those answer ``Unavailable`` with a
reason a seller can act on.
"""

from __future__ import annotations

import re
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
from app.couriers.http import ProviderError, ProviderErrorKind, ProviderProtocolError
from app.couriers.redx.client import REDX_PROVIDER, RedxClient, RedxCredentials
from app.couriers.redx.contract import FIELD_LIMITS, MERCHANT_INVOICE_PATTERN
from app.couriers.redx.dto import RedxArea, RedxCharge, RedxParcel, RedxPickupStore
from app.couriers.redx.mapping import map_parcel_status
from app.couriers.redx.webhooks import parse_redx_events

__all__ = [
    "DEFAULT_WEIGHT_GRAMS",
    "PROVIDER",
    "SUPPORTED_CAPABILITIES",
    "RedxAdapter",
    "build_create_payload",
    "provider_cod_taka",
    "to_provider_phone",
]

log = get_logger(__name__)

PROVIDER = REDX_PROVIDER

#: Capabilities RedX's Open API documents an endpoint for. Anything absent is
#: reported unavailable; nothing is optimistic.
SUPPORTED_CAPABILITIES: frozenset[Capability] = frozenset(
    {
        Capability.CREDENTIAL_VALIDATION,
        Capability.CREATE_SINGLE,
        Capability.STATUS_LOOKUP,
        Capability.CANCEL,
        Capability.LIST_STORES,
        Capability.LOCATION_LOOKUP,
        Capability.PRICE_QUOTE,
        Capability.WEBHOOK,
    }
)

#: Why each unsupported capability is unsupported, in the seller's terms.
_UNSUPPORTED_REASONS: dict[Capability, str] = {
    Capability.CREATE_BULK: (
        "RedX's API has no bulk booking. Book RedX parcels one at a time, each "
        "with its delivery area."
    ),
    Capability.RETURNS: (
        "RedX's API has no return-request call. RedX reports returns in the "
        "parcel's status; start a return from your RedX panel."
    ),
    Capability.BALANCE: "RedX's API has no account-balance call.",
    Capability.PAYOUTS: (
        "RedX's API has no payout statement. Upload your RedX statement to reconcile payouts."
    ),
    Capability.PAYMENTS: "RedX's API has no payments call.",
    Capability.PAYMENT_CONSIGNMENTS: (
        "RedX's API cannot expand a payment into the parcels it covers."
    ),
    Capability.CUSTOMER_STATS: "RedX's API has no customer delivery-history call.",
    Capability.AUTO_ADDRESS: (
        "RedX needs a delivery area chosen from its own list on every booking."
    ),
}

#: ecomsbd's default parcel weight, in grams, when an order carries none.
#: ``parcel_weight`` is required by RedX, so *something* must be sent; this is
#: our policy for the missing case, not a RedX minimum.
DEFAULT_WEIGHT_GRAMS = 500

_INVOICE_RE = re.compile(MERCHANT_INVOICE_PATTERN)


def to_provider_phone(e164: str) -> str:
    """``+8801712345678`` -> ``01712345678``.

    Validates rather than slices: a number that is not a Bangladeshi mobile is
    refused here, before a booking attempt exists, rather than by RedX after a
    parcel might.
    """
    number = try_normalize_bd_phone(e164)
    if number is None:
        raise ValueError(f"Not a valid Bangladeshi mobile number for RedX: {e164[:4]}…")
    return number.national


def provider_cod_taka(cod: Money) -> tuple[int, int]:
    """Split a COD amount into whole taka and the paisa that cannot be collected.

    Returns ``(taka, residual_paisa)``. Poisha coins are out of circulation, so
    ৳1050.50 cannot be collected as stated; the residual is recorded on the
    booking attempt rather than silently dropped.
    """
    taka = int((Decimal(cod.paisa) / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return taka, cod.paisa - taka * 100


def _whole_taka(amount: Money | None) -> int:
    if amount is None or amount.paisa <= 0:
        return 0
    return int((Decimal(amount.paisa) / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _truncate(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    limit = FIELD_LIMITS.get(field_name)
    if limit is None or len(value) <= limit:
        return value
    return value[:limit]


def _validate_invoice(reference: str) -> None:
    """Refuse a reference RedX could not carry back to us.

    ``merchant_invoice_id`` is the only handle that exists before RedX answers,
    so it is the key a person uses to find an unconfirmed booking in RedX's
    panel. A reference that cannot survive the round trip is refused here.
    """
    if not reference:
        raise ValueError("A merchant invoice reference is required")
    if len(reference) > FIELD_LIMITS["merchant_invoice_id"]:
        raise ValueError("That order reference is too long for RedX")
    if not _INVOICE_RE.match(reference):
        raise ValueError("Order references sent to RedX may only contain letters, digits, - and _")


def _area_id(value: str | None) -> int:
    text = (value or "").strip()
    if not text.isdigit() or int(text) <= 0:
        raise ValueError("Choose the RedX delivery area for this order before booking.")
    return int(text)


def build_create_payload(
    req: BookingRequest,
    merchant_reference: str,
    *,
    store_id: str | None = None,
) -> dict[str, Any]:
    """Map a normalized booking onto RedX's documented create fields.

    Types follow the documentation's example request where it and the field
    table disagree (``parcel_weight``, ``value`` and ``pickup_store_id`` are
    numbers there); ``cash_collection_amount`` is a string in both, so it is
    sent as one.
    """
    _validate_invoice(merchant_reference)
    area_id = _area_id(req.delivery_area_id)
    area_name = _truncate((req.delivery_area_name or "").strip(), "delivery_area") or ""
    if not area_name:
        raise ValueError("Choose the RedX delivery area for this order before booking.")

    address = (_truncate(req.recipient_address, "customer_address") or "").strip()
    if not address:
        raise ValueError("RedX needs the customer's delivery address")

    cod_taka, _residual = provider_cod_taka(req.cod_amount)
    weight = req.weight_grams if req.weight_grams and req.weight_grams > 0 else DEFAULT_WEIGHT_GRAMS
    declared = req.declared_value if req.declared_value is not None else req.cod_amount

    payload: dict[str, Any] = {
        "customer_name": _truncate(req.recipient_name.strip(), "customer_name") or "Customer",
        "customer_phone": to_provider_phone(req.recipient_phone_e164),
        "delivery_area": area_name,
        "delivery_area_id": area_id,
        "customer_address": address,
        "merchant_invoice_id": merchant_reference,
        "cash_collection_amount": str(cod_taka),
        "parcel_weight": weight,
        "value": _whole_taka(declared),
    }
    note = (_truncate(req.note, "instruction") or "").strip()
    if note:
        payload["instruction"] = note
    chosen_store = (store_id or "").strip()
    if chosen_store:
        payload["pickup_store_id"] = int(chosen_store) if chosen_store.isdigit() else chosen_store
    return payload


class RedxAdapter:
    """RedX, behind the standard courier interface."""

    provider = PROVIDER

    def __init__(self, client: RedxClient) -> None:
        self._client = client

    @property
    def client(self) -> RedxClient:
        """The typed client, for the RedX calls the generic interface has no shape for."""
        return self._client

    @property
    def bulk_chunk_size(self) -> int:
        """One. RedX has no bulk create, and a batch size is not a claim that it does."""
        return 1

    def describe_booking(self, req: BookingRequest, merchant_reference: str) -> BookingPreview:
        """What would be sent to RedX, masked, plus the COD split.

        Raises ``ValueError`` — before anything is persisted — for a booking
        RedX would refuse: no delivery area, a phone that is not a Bangladeshi
        mobile, a reference outside the character set.
        """
        payload = build_create_payload(req, merchant_reference, store_id=req.store_reference)
        redacted = dict(payload)
        redacted["customer_phone"] = mask_phone(str(payload["customer_phone"]))
        cod_taka, residual = provider_cod_taka(req.cod_amount)
        return BookingPreview(
            redacted_payload=redacted,
            recipient_phone_masked=mask_phone(str(payload["customer_phone"])) or "",
            cod_taka=cod_taka,
            cod_residual_paisa=residual,
        )

    # -------------------------------------------------------- capabilities --

    async def capabilities(self, creds: Any) -> set[Capability]:
        """What RedX documents. Does not call the provider."""
        return set(SUPPORTED_CAPABILITIES)

    # ---------------------------------------------------------- credentials --

    async def validate_credentials(self, creds: Any) -> ValidationResult:
        """Check the token with ``GET /pickup/stores``.

        RedX documents no dedicated "who am I" call, so the check is the safest
        documented read that is specific to the merchant: it lists the
        account's own pickup stores and changes nothing. Creating a parcel to
        test a token would leave a real parcel behind.

        The three outcomes stay apart. A 401/403 is RedX refusing the token; a
        5xx, a timeout or an unreadable body is not evidence about the token
        at all, so the seller is told it could not be checked.
        """
        credentials = _as_credentials(creds)
        try:
            result = await self._client.list_pickup_stores(credentials)
        except ProviderError as exc:
            if exc.is_auth_failure:
                return ValidationResult(
                    valid=False,
                    rejected=True,
                    message=(
                        "RedX rejected this API token. Check it is the "
                        + ("sandbox" if credentials.sandbox else "production")
                        + " token from RedX → Developer API."
                    ),
                    account_label=credentials.masked_identifier,
                )
            return ValidationResult(
                valid=False,
                rejected=False,
                message=_UNVERIFIABLE_MESSAGE,
                account_label=credentials.masked_identifier,
            )

        stores: list[RedxPickupStore] = result.value
        log.info(
            "redx credentials validated",
            extra={
                "provider": PROVIDER,
                "operation": "validate_credentials",
                "correlation_id": result.correlation_id,
                "result": "valid",
                "pickup_stores": len(stores),
            },
        )
        return ValidationResult(
            valid=True,
            message="Connected to RedX.",
            detected_capabilities=frozenset(SUPPORTED_CAPABILITIES),
            account_label=credentials.masked_identifier,
        )

    # ------------------------------------------------------ reference data --

    async def list_stores(self, creds: Any) -> list[Store] | Unavailable:
        """The merchant's RedX pickup stores. Choosing one is optional."""
        result = await self._client.list_pickup_stores(_as_credentials(creds))
        stores: list[RedxPickupStore] = result.value
        return [
            Store(
                provider_store_id=store.id,
                name=store.name,
                address=store.address,
                raw={"area_id": store.area_id, "area_name": store.area_name},
            )
            for store in stores
        ]

    async def list_delivery_areas(
        self,
        creds: Any,
        *,
        district_name: str | None = None,
        post_code: int | None = None,
    ) -> list[RedxArea]:
        """RedX's delivery areas, for the seller to pick one per booking."""
        result = await self._client.list_areas(
            _as_credentials(creds), district_name=district_name, post_code=post_code
        )
        return list(result.value)

    async def quote(self, creds: Any, req: BookingRequest) -> Quote | Unavailable:
        """RedX's charge for this parcel, from ``GET /charge/charge_calculator``.

        The calculator needs the pickup area as well as the delivery area, and
        the pickup area belongs to a pickup store, so a quote needs both a
        chosen delivery area and a chosen pickup store. Without them the honest
        answer is that no quote can be asked for — not a guess.
        """
        if not req.delivery_area_id or not req.store_reference:
            return Unavailable(
                Capability.PRICE_QUOTE,
                "Choose a RedX pickup store and a delivery area to see RedX's charge.",
            )
        credentials = _as_credentials(creds)
        store_call = await self._client.pickup_store_info(credentials, req.store_reference)
        store: RedxPickupStore = store_call.value
        if store.area_id is None:
            return Unavailable(
                Capability.PRICE_QUOTE,
                "RedX did not say which area this pickup store is in.",
            )
        weight = (
            req.weight_grams if req.weight_grams and req.weight_grams > 0 else DEFAULT_WEIGHT_GRAMS
        )
        charge_call = await self._client.charge(
            credentials,
            delivery_area_id=_area_id(req.delivery_area_id),
            pickup_area_id=store.area_id,
            cash_collection_amount=provider_cod_taka(req.cod_amount)[0],
            weight_grams=weight,
        )
        charge: RedxCharge = charge_call.value
        return Quote(
            delivery_fee=Money(charge.delivery_charge_paisa),
            cod_fee=Money(charge.cod_charge_paisa),
            raw=charge.raw,
        )

    # -------------------------------------------------------------- booking --

    async def create_consignment(
        self, creds: Any, req: BookingRequest, merchant_reference: str
    ) -> BookingResult:
        """Book one parcel.

        ``BOOKED``   RedX answered with a tracking id.
        ``FAILED``   RedX answered and refused, or we refused before sending.
        ``UNKNOWN``  anything else. A parcel may exist. Never retried.
        """
        credentials = _as_credentials(creds)
        try:
            payload = build_create_payload(req, merchant_reference, store_id=req.store_reference)
        except ValueError as exc:
            # Our own validation, before anything is sent. Unambiguously safe.
            return BookingResult(
                outcome=BookingOutcome.FAILED,
                error_code=str(ProviderErrorKind.VALIDATION),
                error_message=str(exc),
            )

        try:
            result = await self._client.create_parcel(credentials, payload)
        except ProviderError as exc:
            return _booking_failure(exc)

        tracking_id: str = result.value
        return BookingResult(
            outcome=BookingOutcome.BOOKED,
            consignment=ProviderConsignment(
                # RedX issues one identifier per parcel. It is both the id
                # every later call takes and the code a seller tracks by.
                provider_consignment_id=tracking_id,
                tracking_code=tracking_id,
                merchant_reference=merchant_reference,
                # The create response carries no status.
                raw_status=None,
                charge=None,
                raw=dict(result.raw_body) if isinstance(result.raw_body, dict) else {},
            ),
            provider_request_id=result.correlation_id,
        )

    async def create_bulk(
        self, creds: Any, reqs: list[BookingRequest]
    ) -> list[BookingResult] | Unavailable:
        """Refused per item, and nothing is sent.

        A list of clean failures rather than ``Unavailable``: the booking
        service reads a non-list answer as "the batch's outcome is unknown" and
        would park every order in ``BOOKING_UNKNOWN`` — for parcels that
        provably never left this process.
        """
        return [
            BookingResult(
                outcome=BookingOutcome.FAILED,
                error_code=str(ProviderErrorKind.VALIDATION),
                error_message=_UNSUPPORTED_REASONS[Capability.CREATE_BULK],
            )
            for _ in reqs
        ]

    # --------------------------------------------------------------- status --

    async def get_status(self, creds: Any, reference: str) -> ProviderStatus | Unavailable:
        """``GET /parcel/info/{tracking_id}``: RedX's current status for a parcel.

        ``raw`` carries the parcel record, including ``delivery_type``, which
        the mapping needs to tell a whole delivery from a partial one.
        """
        result = await self._client.parcel_info(_as_credentials(creds), reference)
        parcel: RedxParcel = result.value
        if parcel.status is None:
            raise ProviderProtocolError(
                "RedX returned the parcel without a status",
                provider=PROVIDER,
                http_status=result.http_status,
                reached_provider=True,
                correlation_id=result.correlation_id,
            )
        mapping = map_parcel_status(parcel.status, parcel.delivery_type)
        return ProviderStatus(
            provider_consignment_id=parcel.tracking_id,
            raw_status=parcel.status,
            normalized_status=str(mapping.canonical) if mapping.canonical else None,
            raw=dict(parcel.raw),
        )

    async def cancel(self, creds: Any, reference: str) -> bool | Unavailable:
        """Ask RedX to cancel a parcel. ``True`` only when RedX says it accepted.

        Sent once. A lost answer raises :class:`ProviderAmbiguousError` from the
        client rather than returning ``False``: "we do not know" is not "RedX
        refused".
        """
        result = await self._client.cancel_parcel(_as_credentials(creds), reference)
        success, _message = result.value
        if success is None:
            raise ProviderProtocolError(
                "RedX's answer to the cancellation could not be read",
                provider=PROVIDER,
                http_status=result.http_status,
                reached_provider=True,
                correlation_id=result.correlation_id,
            )
        return success

    # ------------------------------------------------------------- webhooks --

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        """Always ``False`` *on this interface*.

        RedX's callback credential is per shop and arrives in the URL, and this
        protocol method has neither in scope. Verification happens in
        :class:`~app.couriers.redx.webhooks.RedxWebhookVerifier`, which the
        route builds with that shop's secret and the token from the request.
        """
        return False

    def parse_webhook(self, headers: dict[str, str], body: bytes) -> list[ProviderEvent]:
        return [
            ProviderEvent(
                provider_consignment_id=event.provider_consignment_id or "",
                raw_status=event.raw_status or "",
                occurred_at=event.occurred_at,
                provider_event_id=None,
                normalized_status=(
                    str(canonical)
                    if (
                        canonical := map_parcel_status(
                            event.raw_status, event.delivery_type
                        ).canonical
                    )
                    else None
                ),
                raw=event.raw,
            )
            for event in parse_redx_events(body)
        ]

    # ---------------------------------------------- documented as absent --

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


_UNVERIFIABLE_MESSAGE = (
    "RedX did not answer, so this token could not be checked. It has not been "
    "marked wrong — try again shortly."
)


def _booking_failure(exc: ProviderError) -> BookingResult:
    """Turn a client exception into the right booking outcome.

    ``create_may_have_succeeded`` defaults to the unsafe answer, so anything we
    are not *sure* about becomes ``UNKNOWN`` and goes to reconciliation rather
    than being re-sent.
    """
    outcome = BookingOutcome.UNKNOWN if exc.create_may_have_succeeded else BookingOutcome.FAILED
    return BookingResult(
        outcome=outcome,
        error_code=str(exc.kind),
        error_message=str(exc),
        provider_request_id=exc.correlation_id,
    )


def _as_credentials(creds: Any) -> RedxCredentials:
    if isinstance(creds, RedxCredentials):
        return creds
    if isinstance(creds, dict):
        return RedxCredentials(
            api_token=str(creds["api_token"]), sandbox=bool(creds.get("sandbox", False))
        )
    raise TypeError("RedX needs RedxCredentials or a dict with api_token")
