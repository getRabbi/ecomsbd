"""The Pathao courier adapter.

Implements :class:`~app.couriers.adapter.CourierAdapter`. Everything
Pathao-specific stops here: field names, phone shape, weight units, the
store requirement, the numeric delivery and item codes. Order, money and profit
code sees only the normalized types from :mod:`app.couriers.adapter` and never
branches on a provider name.

Three boundary transforms live here, for the same reason they do in the
Steadfast adapter — they are the places where a unit or a shape changes, and a
unit that changes in two places eventually changes differently in each:

*   **Phone.** ecomsbd stores ``+8801XXXXXXXXX``; Pathao takes an 11-digit
    national number.
*   **Money.** ecomsbd stores paisa; ``amount_to_collect`` is taka, and Pathao's
    own plugin casts it to an integer. The paisa remainder is returned, not
    dropped, so a receivable is never quietly a few paisa from the cash that
    can actually arrive.
*   **Weight.** ecomsbd stores grams; ``item_weight`` is kilograms.

The capability split is the honest one. Pathao's published integration creates
parcels and receives webhooks; it never polls for status, quotes a price,
cancels, or reads a balance. Those are reported ``Unavailable`` with a reason a
seller can act on, not attempted against a guessed path.
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
from app.couriers.http import ProviderError, ProviderErrorKind
from app.couriers.pathao.client import (
    PATHAO_PROVIDER,
    PathaoClient,
    PathaoCredentials,
)
from app.couriers.pathao.contract import (
    ADDRESS_MIN_LENGTH,
    FIELD_LIMITS,
    DeliveryType,
    ItemType,
    validate_merchant_order_id,
)
from app.couriers.pathao.dto import CreateOrderResponse, PathaoStore
from app.couriers.pathao.webhooks import parse_pathao_events

__all__ = [
    "PROVIDER",
    "SUPPORTED_CAPABILITIES",
    "PathaoAdapter",
    "build_create_payload",
    "provider_cod_taka",
    "to_provider_phone",
    "to_provider_weight_kg",
]

log = get_logger(__name__)

PROVIDER = PATHAO_PROVIDER

#: Capabilities Pathao's published integration has an endpoint for. Anything
#: absent from this set is reported unavailable; nothing is optimistic.
SUPPORTED_CAPABILITIES: frozenset[Capability] = frozenset(
    {
        Capability.CREDENTIAL_VALIDATION,
        Capability.CREATE_SINGLE,
        Capability.CREATE_BULK,
        Capability.LIST_STORES,
        Capability.LOCATION_LOOKUP,
        Capability.WEBHOOK,
        Capability.AUTO_ADDRESS,
    }
)

#: Why each unsupported capability is unsupported, in the seller's terms. The
#: UI shows these, so they say what to do instead rather than naming a section.
_UNSUPPORTED_REASONS: dict[Capability, str] = {
    Capability.STATUS_LOOKUP: (
        "Pathao's published integration has no status lookup — it sends updates "
        "by webhook instead. Connect the Pathao webhook to keep parcel status "
        "current."
    ),
    Capability.PRICE_QUOTE: (
        "Pathao's published integration has no delivery-charge quote endpoint. "
        "Use your configured rate card for estimates; the actual fee arrives "
        "with the booking and the delivery updates."
    ),
    Capability.CANCEL: (
        "Pathao's published integration has no cancel endpoint. Cancel the "
        "parcel from your Pathao merchant panel."
    ),
    Capability.RETURNS: (
        "Pathao's published integration has no return-request endpoint. Pathao "
        "reports returns to ecomsbd by webhook, but a return cannot be started "
        "from here."
    ),
    Capability.BALANCE: ("Pathao's published integration has no account-balance endpoint."),
    Capability.PAYOUTS: (
        "Pathao's published integration has no payout statement API. Upload "
        "your Pathao statement to reconcile payouts."
    ),
    Capability.PAYMENTS: (
        "Pathao's published integration has no payments endpoint. Pathao sends "
        "a payment-invoice webhook, which ecomsbd records."
    ),
    Capability.PAYMENT_CONSIGNMENTS: (
        "Pathao's published integration cannot expand a payment into the parcels it covers."
    ),
    Capability.CUSTOMER_STATS: (
        "Pathao's published integration has no customer delivery-history endpoint."
    ),
    Capability.CREATE_BULK: "",  # supported; present only to keep the map total
}

#: ecomsbd's default parcel weight, in grams, when an order carries none.
#: ``item_weight`` is a required Pathao field, so *something* must be sent; this
#: is our policy for the missing case, not a Pathao minimum.
DEFAULT_WEIGHT_GRAMS = 500


def to_provider_phone(e164: str) -> str:
    """``+8801712345678`` -> ``01712345678``.

    Validates rather than slices: a number that is not a valid Bangladeshi
    mobile raises here, before a booking attempt is persisted, rather than
    being rejected by Pathao after a parcel may already exist.
    """
    number = try_normalize_bd_phone(e164)
    if number is None:
        raise ValueError(f"Not a valid Bangladeshi mobile number for Pathao: {e164[:4]}…")
    return number.national


def provider_cod_taka(cod: Money) -> tuple[int, int]:
    """Split a COD amount into whole taka and the paisa that cannot be collected.

    Returns ``(taka, residual_paisa)``.

    Pathao's own plugin casts ``amount_to_collect`` to an integer, and a courier
    collects banknotes: poisha coins are out of circulation, so a COD of
    ৳1050.50 cannot be collected as stated whatever the field type allows. The
    residual is **returned rather than dropped**, so the booking records what
    the courier was actually asked for.
    """
    taka = int((Decimal(cod.paisa) / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    residual = cod.paisa - taka * 100
    return taka, residual


def to_provider_weight_kg(weight_grams: int | None) -> float:
    """Grams -> kilograms, to two decimals.

    No ceiling is applied. Pathao's merchant panel is widely reported to accept
    0.5–10 kg, but that range appears in no source ecomsbd has verified, and
    silently clamping a real 12 kg parcel to 10 would misdeclare it.
    """
    grams = weight_grams if weight_grams and weight_grams > 0 else DEFAULT_WEIGHT_GRAMS
    return round(grams / 1000, 2)


def _truncate(value: str | None, field_name: str) -> str | None:
    """Enforce a length limit at the boundary.

    Truncation rather than rejection for descriptive fields: a 260-character
    address is a real address with a long landmark in it, and refusing the
    booking would help nobody. The full value stays on the order.
    """
    if value is None:
        return None
    limit = FIELD_LIMITS.get(field_name)
    if limit is None or len(value) <= limit:
        return value
    return value[:limit]


def build_create_payload(
    req: BookingRequest,
    merchant_reference: str,
    *,
    store_id: str,
    delivery_type: DeliveryType = DeliveryType.NORMAL,
    item_type: ItemType = ItemType.PARCEL,
) -> dict[str, Any]:
    """Map a normalized booking onto Pathao's published create fields.

    The three location ids are **omitted**, not sent empty. Pathao's own plugin
    only includes ``recipient_city``/``recipient_zone``/``recipient_area`` when
    they are non-empty, which is what makes a sufficiently complete free-text
    address the primary path and structured area selection the fallback.
    """
    validate_merchant_order_id(merchant_reference)

    if not store_id:
        raise ValueError("A Pathao pickup store must be selected before booking")

    address = _truncate(req.recipient_address, "recipient_address") or ""
    if len(address.strip()) < ADDRESS_MIN_LENGTH:
        raise ValueError(
            f"Pathao needs a delivery address of at least {ADDRESS_MIN_LENGTH} characters"
        )

    secondary = (
        to_provider_phone(req.recipient_alternate_phone_e164)
        if req.recipient_alternate_phone_e164
        else ""
    )

    payload: dict[str, Any] = {
        "store_id": int(store_id) if str(store_id).isdigit() else store_id,
        "merchant_order_id": merchant_reference,
        "recipient_name": _truncate(req.recipient_name, "recipient_name") or "",
        "recipient_phone": to_provider_phone(req.recipient_phone_e164),
        "recipient_secondary_phone": secondary,
        "recipient_address": address,
        "delivery_type": delivery_type.wire_value,
        "item_type": item_type.wire_value,
        "special_instruction": _truncate(req.note, "special_instruction") or "",
        "item_quantity": req.item_quantity if req.item_quantity > 0 else 1,
        "item_weight": to_provider_weight_kg(req.weight_grams),
        "item_description": _truncate(req.item_description, "item_description") or "",
        "amount_to_collect": provider_cod_taka(req.cod_amount)[0],
    }
    return payload


class PathaoAdapter:
    """Pathao Courier, behind the standard courier interface."""

    provider = PROVIDER

    def __init__(self, client: PathaoClient) -> None:
        self._client = client

    @property
    def client(self) -> PathaoClient:
        """The typed client, for the provider-specific calls the domain needs.

        Store and location selection reach through here for endpoints the
        generic adapter interface has no shape for.
        """
        return self._client

    @property
    def bulk_chunk_size(self) -> int:
        return self._client.config.bulk_chunk_size

    def describe_booking(self, req: BookingRequest, merchant_reference: str) -> BookingPreview:
        """What would be sent to Pathao, masked, plus the COD split.

        The stored payload is Pathao-shaped — the fields Pathao's own create
        actually takes — so the evidence record describes the call that was
        made rather than some other provider's idea of it.
        """
        payload = build_create_payload(req, merchant_reference, store_id=req.store_reference or "")
        redacted = dict(payload)
        for key in ("recipient_phone", "recipient_secondary_phone"):
            if redacted.get(key):
                redacted[key] = mask_phone(str(redacted[key]))
        cod_taka, residual = provider_cod_taka(req.cod_amount)
        return BookingPreview(
            redacted_payload=redacted,
            recipient_phone_masked=mask_phone(str(payload["recipient_phone"])),
            cod_taka=cod_taka,
            cod_residual_paisa=residual,
        )

    # -------------------------------------------------------- capabilities --

    async def capabilities(self, creds: Any) -> set[Capability]:
        """What Pathao can do, from its published integration alone.

        Does not call the provider. Pathao exposes no capability-discovery
        endpoint, and probing by attempting each one would mean creating a
        parcel to find out whether create works.
        """
        return set(SUPPORTED_CAPABILITIES)

    # ---------------------------------------------------------- credentials --

    async def validate_credentials(self, creds: Any) -> ValidationResult:
        """Check credentials with the safest published call.

        A login followed by ``GET /user/short-info`` reads a profile and changes
        nothing. Creating a parcel to test a key would leave a real parcel and a
        real charge behind.

        The three outcomes are kept apart deliberately. A rejected credential is
        the seller's problem to fix; a 500 is ours to wait out; and an
        unreadable response is neither, so it says so rather than picking the
        convenient one.
        """
        credentials = _as_credentials(creds)
        try:
            result = await self._client.user_short_info(credentials)
        except ProviderError as exc:
            if exc.is_auth_failure:
                return ValidationResult(
                    valid=False,
                    rejected=True,
                    message="Pathao rejected this Client ID and Client Secret.",
                    account_label=credentials.masked_identifier,
                )
            # Transient or unreadable. Explicitly *not* invalid: marking an
            # account bad because the provider had a bad minute would make a
            # seller re-enter working credentials.
            return ValidationResult(
                valid=False,
                rejected=False,
                message=_UNVERIFIABLE_MESSAGE,
                account_label=credentials.masked_identifier,
                detected_capabilities=frozenset(),
            )

        log.info(
            "pathao credentials validated",
            extra={
                "provider": PROVIDER,
                "operation": "validate_credentials",
                "correlation_id": result.correlation_id,
                "result": "valid",
            },
        )
        return ValidationResult(
            valid=True,
            message="Connected to Pathao.",
            detected_capabilities=frozenset(SUPPORTED_CAPABILITIES),
            account_label=credentials.masked_identifier,
        )

    # ---------------------------------------------------------------- stores --

    async def list_stores(self, creds: Any) -> list[Store] | Unavailable:
        """The merchant's pickup stores.

        Required before booking: ``store_id`` is a mandatory create field, and
        Pathao provides no default.
        """
        credentials = _as_credentials(creds)
        result = await self._client.list_stores(credentials)
        stores: list[PathaoStore] = result.value
        return [
            Store(
                provider_store_id=store.store_id,
                name=store.store_name,
                address=store.address,
                raw=store.raw,
            )
            for store in stores
        ]

    # -------------------------------------------------------------- booking --

    async def create_consignment(
        self, creds: Any, req: BookingRequest, merchant_reference: str
    ) -> BookingResult:
        """Book one parcel.

        Returns rather than raises, because the *kind* of failure is the
        result. Three outcomes, and the difference between the last two is the
        whole point:

        ``BOOKED``   Pathao confirmed, with a consignment id.
        ``FAILED``   Pathao answered and refused. Nothing exists.
        ``UNKNOWN``  anything else. A parcel may exist. Never retried.
        """
        credentials = _as_credentials(creds)
        try:
            payload = build_create_payload(
                req, merchant_reference, store_id=req.store_reference or ""
            )
        except ValueError as exc:
            # Our own validation, before anything is sent. Unambiguously safe.
            return BookingResult(
                outcome=BookingOutcome.FAILED,
                error_code=str(ProviderErrorKind.VALIDATION),
                error_message=str(exc),
            )

        try:
            result = await self._client.create_order(credentials, payload)
        except ProviderError as exc:
            return _booking_failure(exc)

        response: CreateOrderResponse = result.value
        return BookingResult(
            outcome=BookingOutcome.BOOKED,
            consignment=ProviderConsignment(
                provider_consignment_id=response.consignment_id,
                # Pathao's create response carries no separate tracking code;
                # the consignment id is the reference a seller is given.
                tracking_code=None,
                merchant_reference=response.merchant_order_id or merchant_reference,
                raw_status=response.order_status,
                # Deliberately not a charge. `delivery_fee` is Pathao's quoted
                # fee at booking time, not a settled cost, and treating a quote
                # as a cost is how profit drifts from truth.
                charge=None,
                raw=response.raw,
            ),
            provider_request_id=result.correlation_id,
        )

    async def create_bulk(
        self, creds: Any, reqs: list[BookingRequest]
    ) -> list[BookingResult] | Unavailable:
        """Book a chunk of parcels in one request.

        Pathao publishes no per-item response schema for ``/orders/bulk`` — its
        own plugin passes the body straight through without reading it. So the
        honest outcome for every item that was actually sent is **ambiguous**,
        resolved against the merchant reference, rather than a success we
        cannot evidence. Sending one request and reporting a knowable state is
        worth more than parsing a shape nobody has published.
        """
        if not reqs:
            return []

        credentials = _as_credentials(creds)
        prepared: list[tuple[BookingRequest, dict[str, Any] | None, str | None]] = []
        payloads: list[dict[str, Any]] = []

        for req in reqs:
            try:
                built = build_create_payload(
                    req, req.merchant_reference, store_id=req.store_reference or ""
                )
            except ValueError as exc:
                prepared.append((req, None, str(exc)))
                continue
            payloads.append(built)
            prepared.append((req, built, None))

        if not payloads:
            # Every item failed our own validation. Nothing is sent.
            return [
                BookingResult(
                    outcome=BookingOutcome.FAILED,
                    error_code=str(ProviderErrorKind.VALIDATION),
                    error_message=error,
                )
                for _, _, error in prepared
            ]

        try:
            result = await self._client.create_order_bulk(credentials, payloads)
        except ProviderError as exc:
            failure = _booking_failure(exc)
            # An item rejected by our own validation keeps its safe failure: it
            # was never in the payload, so it cannot be ambiguous.
            return [
                BookingResult(
                    outcome=BookingOutcome.FAILED,
                    error_code=str(ProviderErrorKind.VALIDATION),
                    error_message=error,
                )
                if error is not None
                else failure
                for _, _, error in prepared
            ]

        return [
            BookingResult(
                outcome=BookingOutcome.FAILED,
                error_code=str(ProviderErrorKind.VALIDATION),
                error_message=error,
            )
            if error is not None
            else BookingResult(
                outcome=BookingOutcome.UNKNOWN,
                error_code=str(ProviderErrorKind.AMBIGUOUS),
                error_message=(
                    "Pathao accepted the batch but publishes no per-parcel result. "
                    "Each parcel is confirmed from its delivery updates."
                ),
                provider_request_id=result.correlation_id,
            )
            for _, _, error in prepared
        ]

    # ------------------------------------------------------------- webhooks --

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        """Always ``False`` *on this interface*, and that is not a refusal.

        Pathao's webhook signature is a **per-merchant shared secret**, and this
        protocol method has no account in scope — so there is nothing here to
        compare against. Verification happens in
        :class:`~app.couriers.pathao.webhooks.PathaoWebhookVerifier`, which is
        constructed with that shop's secret.

        Returning ``True`` here to mean "Pathao supports webhooks" would be the
        one mistake worth preventing: it would accept unauthenticated requests
        that move money.
        """
        return False

    def parse_webhook(self, headers: dict[str, str], body: bytes) -> list[ProviderEvent]:
        """Parse a Pathao callback body into normalized events.

        Parsing is not trusting. This is safe to run on an unverified body
        because the caller only acts on events from a body that
        :class:`PathaoWebhookVerifier` has already accepted.
        """
        return [
            ProviderEvent(
                provider_consignment_id=event.provider_consignment_id or "",
                raw_status=event.raw_status or "",
                occurred_at=event.occurred_at,
                provider_event_id=event.provider_event_id,
                normalized_status=None,
                raw=event.raw,
            )
            for event in parse_pathao_events(body)
        ]

    # ------------------------------------------- published as absent --

    async def get_status(self, creds: Any, reference: str) -> ProviderStatus | Unavailable:
        """Not available.

        Pathao's published integration never polls: it keeps parcel state
        current from webhooks alone. Inventing an ``/orders/{id}/info`` path
        from a community SDK would be a guessed endpoint called against a live
        merchant account. See ``PATHAO_STATUS_LOOKUP_CONTRACT_REQUIRED``.
        """
        return Unavailable(Capability.STATUS_LOOKUP, _UNSUPPORTED_REASONS[Capability.STATUS_LOOKUP])

    async def quote(self, creds: Any, req: BookingRequest) -> Quote | Unavailable:
        return Unavailable(Capability.PRICE_QUOTE, _UNSUPPORTED_REASONS[Capability.PRICE_QUOTE])

    async def cancel(self, creds: Any, reference: str) -> bool | Unavailable:
        return Unavailable(Capability.CANCEL, _UNSUPPORTED_REASONS[Capability.CANCEL])

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
    "Pathao did not answer, so these credentials could not be checked. "
    "They have not been marked wrong — try again shortly."
)


def _booking_failure(exc: ProviderError) -> BookingResult:
    """Turn a client exception into the right booking outcome.

    The single most important branch in this file. ``create_may_have_succeeded``
    is the transport's and the client's combined judgement, and it defaults to
    the unsafe answer — so anything we are not *sure* about becomes ``UNKNOWN``
    and goes to reconciliation rather than being re-sent.
    """
    if exc.create_may_have_succeeded:
        return BookingResult(
            outcome=BookingOutcome.UNKNOWN,
            error_code=str(exc.kind),
            error_message=str(exc),
            provider_request_id=exc.correlation_id,
        )
    return BookingResult(
        outcome=BookingOutcome.FAILED,
        error_code=str(exc.kind),
        error_message=str(exc),
        provider_request_id=exc.correlation_id,
    )


def _as_credentials(creds: Any) -> PathaoCredentials:
    if isinstance(creds, PathaoCredentials):
        return creds
    if isinstance(creds, dict):
        return PathaoCredentials(
            client_id=str(creds["client_id"]),
            client_secret=str(creds["client_secret"]),
            sandbox=bool(creds.get("sandbox", False)),
        )
    raise TypeError("Pathao needs PathaoCredentials or a dict with client_id and client_secret")
