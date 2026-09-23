"""The Steadfast courier adapter.

Implements :class:`~app.couriers.adapter.CourierAdapter`. Everything
Steadfast-specific stops here: field names, phone shape, status strings, the
three lookup endpoints, the bulk envelope. Order, money and profit code sees
only the normalized types from :mod:`app.couriers.adapter` and never branches on
a provider name (master spec section 34).

The two boundary transforms live here for the same reason:

*   **Phone.** ecomsbd stores ``+8801XXXXXXXXX`` canonically and always will
    (brief section 6). Steadfast documents an 11-digit national number. The
    conversion happens in :func:`to_provider_phone`, on the way out, once.
*   **Capabilities.** A capability that the supplied document does not describe
    is reported ``Unavailable`` with the reason, never attempted. A provider
    with no documented price-quote endpoint must make the UI show "no quote
    available", not a spinner that never resolves.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.common.money import BDT, Money
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
from app.couriers.steadfast.client import (
    StatusLookupKind,
    SteadfastCallResult,
    SteadfastClient,
    SteadfastCredentials,
)
from app.couriers.steadfast.contract import (
    FIELD_LIMITS,
    DeliveryType,
    validate_invoice,
)
from app.couriers.steadfast.dto import (
    BulkCreateResult,
    CreateOrderRequest,
    CreateOrderResponse,
    ReturnRequestRecord,
    StatusResponse,
)
from app.couriers.steadfast.errors import (
    SteadfastError,
    SteadfastErrorKind,
)
from app.couriers.steadfast.mapping import map_delivery_status

__all__ = [
    "SUPPORTED_CAPABILITIES",
    "SteadfastAdapter",
    "build_create_request",
    "provider_cod_taka",
    "to_provider_phone",
]

log = get_logger(__name__)

PROVIDER = "steadfast"

#: Capabilities the supplied V1 document describes an endpoint for. Anything
#: absent from this set is reported unavailable; nothing is optimistic.
SUPPORTED_CAPABILITIES: frozenset[Capability] = frozenset(
    {
        Capability.CREDENTIAL_VALIDATION,
        Capability.CREATE_SINGLE,
        Capability.CREATE_BULK,
        Capability.STATUS_LOOKUP,
        Capability.BALANCE,
        Capability.RETURNS,
        Capability.PAYMENTS,
        Capability.PAYMENT_CONSIGNMENTS,
        Capability.LOCATION_LOOKUP,
    }
)

#: Why each unsupported capability is unsupported, in the seller's terms. The
#: UI shows these, so they say what to do instead rather than naming a section.
_UNSUPPORTED_REASONS: dict[Capability, str] = {
    Capability.WEBHOOK: (
        "Steadfast's V1 documentation describes no webhook. ecomsbd keeps parcel "
        "status up to date by polling instead."
    ),
    Capability.PRICE_QUOTE: (
        "Steadfast's V1 documentation has no delivery-charge quote endpoint. "
        "Use your configured rate card for estimates."
    ),
    Capability.CUSTOMER_STATS: (
        "Steadfast's V1 documentation has no customer delivery-history endpoint."
    ),
    Capability.AUTO_ADDRESS: (
        "Steadfast takes the address as free text; there is no address lookup to call."
    ),
    Capability.CANCEL: (
        "Steadfast's V1 documentation has no cancel endpoint. Request a return instead."
    ),
    Capability.LIST_STORES: ("Steadfast's V1 documentation has no pickup-store listing."),
    Capability.PAYOUTS: (
        "Steadfast reports payments through its payments endpoints, which ecomsbd "
        "syncs; there is no separate payout statement API."
    ),
}


def to_provider_phone(e164: str) -> str:
    """``+8801712345678`` -> ``01712345678``.

    The document says "Must be 11 Digits Phone number" and its example is
    ``01234567890``. This is the only place that transformation happens, and it
    validates rather than slices: a number that is not a valid Bangladeshi
    mobile raises here, before a booking attempt is persisted, rather than
    being rejected by the provider after a parcel may already exist.
    """
    number = try_normalize_bd_phone(e164)
    if number is None:
        raise ValueError(f"Not a valid Bangladeshi mobile number for Steadfast: {e164[:4]}…")
    return number.national


def provider_cod_taka(cod: Money) -> tuple[int, int]:
    """Split a COD amount into the whole taka Steadfast is asked to collect,
    and the paisa remainder that cannot be handed over.

    Returns ``(taka, residual_paisa)``.

    Steadfast documents ``cod_amount`` as ``numeric`` with the example
    ``1060`` — taka, not paisa — and a courier collects banknotes. Poisha coins
    are out of circulation in Bangladesh, so a COD of ৳1050.50 cannot be
    collected as stated whatever the field type allows.

    Rounding is the system's central half-up rule, and the residual is
    **returned rather than dropped**: the booking records what the courier was
    actually asked for, so a receivable is never quietly a few paisa away from
    the cash that can arrive. Silently rounding is how a reconciliation
    difference appears months later with nobody able to explain it.
    """
    taka = _quantize_half_up(Decimal(cod.paisa) / 100)
    residual = cod.paisa - taka * 100
    return taka, residual


def _quantize_half_up(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _truncate(value: str | None, field_name: str) -> str | None:
    """Enforce a documented character limit, at the boundary.

    Truncation rather than rejection, for the fields where the document gives a
    limit and the content is descriptive. A 260-character address is a real
    address with a long landmark in it; refusing the booking would help nobody,
    and the courier's own limit is what it is. The full value stays on the
    order.
    """
    if value is None:
        return None
    limit = FIELD_LIMITS.get(field_name)
    if limit is None or len(value) <= limit:
        return value
    return value[:limit]


def build_create_request(
    req: BookingRequest, merchant_reference: str, *, delivery_type: DeliveryType | None = None
) -> CreateOrderRequest:
    """Map a normalized booking onto the documented Steadfast fields.

    ``cod_amount`` goes out in **taka**, not paisa: the document says "Cash on
    delivery amount in BDT" and its example is ``1060``, which is taka. Sending
    paisa would ask the courier to collect a hundred times the order value.
    """
    validate_invoice(merchant_reference)

    alternate = (
        to_provider_phone(req.recipient_alternate_phone_e164)
        if req.recipient_alternate_phone_e164
        else None
    )
    return CreateOrderRequest(
        invoice=merchant_reference,
        recipient_name=_truncate(req.recipient_name, "recipient_name") or "",
        recipient_phone=to_provider_phone(req.recipient_phone_e164),
        recipient_address=_truncate(req.recipient_address, "recipient_address") or "",
        cod_amount=provider_cod_taka(req.cod_amount)[0],
        alternative_phone=alternate,
        note=req.note,
        item_description=req.item_description or None,
        total_lot=req.item_quantity if req.item_quantity > 0 else None,
        delivery_type=delivery_type.wire_value if delivery_type is not None else None,
    )


class SteadfastAdapter:
    """Steadfast V1, behind the standard courier interface."""

    provider = PROVIDER

    def __init__(self, client: SteadfastClient) -> None:
        self._client = client

    @property
    def client(self) -> SteadfastClient:
        """The typed client, for the provider-specific calls the domain needs.

        Booking recovery, payment sync and the smoke tool reach through here
        for endpoints the generic adapter interface has no shape for — status
        *by invoice*, payment detail with consignments. That is deliberate:
        flattening those into the generic protocol would either lose the
        distinction or force every provider to grow a method it cannot serve.
        """
        return self._client

    @property
    def bulk_chunk_size(self) -> int:
        return self._client.config.bulk_chunk_size

    def describe_booking(self, req: BookingRequest, merchant_reference: str) -> BookingPreview:
        """What would be sent, masked, plus the COD split.

        Delegates to :func:`build_create_request` and
        :func:`provider_cod_taka`, which is what the booking service used to
        call directly. Moving the call behind this method is the whole point:
        the generic service no longer reaches into one provider's module to
        describe every provider's booking.
        """
        payload = build_create_request(req, merchant_reference)
        cod_taka, residual = provider_cod_taka(req.cod_amount)
        return BookingPreview(
            redacted_payload=payload.redacted(),
            recipient_phone_masked=mask_phone(payload.recipient_phone) or "",
            cod_taka=cod_taka,
            cod_residual_paisa=residual,
        )

    # -------------------------------------------------------- capabilities --

    async def capabilities(self, creds: Any) -> set[Capability]:
        """What this provider can do, from the document alone.

        Does not call the provider. Steadfast exposes no capability-discovery
        endpoint, and probing by attempting each one would mean creating a
        parcel to find out whether create works.
        """
        return set(SUPPORTED_CAPABILITIES)

    # ---------------------------------------------------------- credentials --

    async def validate_credentials(self, creds: Any) -> ValidationResult:
        """Check credentials with the safest documented call.

        ``GET /get_balance`` reads one number and changes nothing. Creating a
        parcel to test a key would leave a real parcel and a real charge behind
        (brief section 5).

        The three outcomes are kept apart deliberately. A rejected credential
        is the seller's problem to fix; a 500 is ours to wait out; and an
        unreadable response is neither, so it says so rather than picking the
        convenient one.
        """
        credentials = _as_credentials(creds)
        try:
            result = await self._client.get_balance(credentials)
        except SteadfastError as exc:
            if exc.is_auth_failure:
                return ValidationResult(
                    valid=False,
                    rejected=True,
                    message="Steadfast rejected this API key and secret key.",
                    account_label=credentials.masked_identifier,
                )
            # Transient or unreadable. Explicitly *not* invalid: marking an
            # account bad because the provider had a bad minute would make a
            # seller re-enter working credentials.
            # Transient or unreadable: `rejected` stays False, which is what
            # stops the account being marked bad.
            return ValidationResult(
                valid=False,
                rejected=False,
                message=_UNVERIFIABLE_MESSAGE,
                account_label=credentials.masked_identifier,
                detected_capabilities=frozenset(),
            )

        log.info(
            "steadfast credentials validated",
            extra={
                "provider": PROVIDER,
                "operation": "validate_credentials",
                "correlation_id": result.correlation_id,
                "result": "valid",
            },
        )
        return ValidationResult(
            valid=True,
            message="Connected to Steadfast.",
            detected_capabilities=frozenset(SUPPORTED_CAPABILITIES),
            account_label=credentials.masked_identifier,
            # The check *is* the balance read, so the figure is kept rather
            # than fetched again for the settings screen.
            reported_balance_paisa=int(result.value),
        )

    # -------------------------------------------------------------- booking --

    async def create_consignment(
        self, creds: Any, req: BookingRequest, merchant_reference: str
    ) -> BookingResult:
        """Book one parcel.

        Returns rather than raises, because the *kind* of failure is the
        result. Three outcomes, and the difference between the last two is the
        whole point:

        ``BOOKED``   the provider confirmed, with an id.
        ``FAILED``   the provider answered and refused. Nothing exists.
        ``UNKNOWN``  anything else. A parcel may exist. Never retried.
        """
        credentials = _as_credentials(creds)
        try:
            payload = build_create_request(req, merchant_reference)
        except ValueError as exc:
            # Our own validation, before anything is sent. Unambiguously safe.
            return BookingResult(
                outcome=BookingOutcome.FAILED,
                error_code=str(SteadfastErrorKind.VALIDATION),
                error_message=str(exc),
            )

        try:
            result = await self._client.create_order(credentials, payload)
        except SteadfastError as exc:
            return _booking_failure(exc)

        response: CreateOrderResponse = result.value
        return BookingResult(
            outcome=BookingOutcome.BOOKED,
            consignment=ProviderConsignment(
                provider_consignment_id=response.consignment_id,
                tracking_code=response.tracking_code,
                merchant_reference=response.invoice,
                raw_status=response.provider_status,
                # No charge: the document gives no per-parcel fee on a create
                # response, and `cod_amount` is what to collect, not what it
                # costs (brief section 26).
                charge=None,
                raw=response.raw,
            ),
            provider_request_id=result.correlation_id,
        )

    async def create_bulk(
        self, creds: Any, reqs: list[BookingRequest]
    ) -> list[BookingResult] | Unavailable:
        """Book a chunk of parcels in one request.

        Chunking across multiple requests is the *service's* job, because only
        it can persist a booking attempt before each chunk goes out. This
        method sends exactly one request and never more.

        Results are matched back **by invoice**, never by position. The
        document guarantees no ordering, and a provider that reorders or drops
        an entry would otherwise attribute one order's consignment id to a
        different order.
        """
        if not reqs:
            return []

        credentials = _as_credentials(creds)
        payloads: list[CreateOrderRequest] = []
        prepared: list[tuple[BookingRequest, CreateOrderRequest | None, str | None]] = []

        for req in reqs:
            try:
                built = build_create_request(req, req.merchant_reference)
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
                    error_code=str(SteadfastErrorKind.VALIDATION),
                    error_message=error,
                )
                for _, _, error in prepared
            ]

        try:
            result = await self._client.create_order_bulk(credentials, payloads)
        except SteadfastError as exc:
            failure = _booking_failure(exc)
            # Every item that was actually sent inherits the request's outcome.
            # A locally-rejected item keeps its own safe failure: it was never
            # in the payload, so it cannot be ambiguous.
            return [
                BookingResult(
                    outcome=BookingOutcome.FAILED,
                    error_code=str(SteadfastErrorKind.VALIDATION),
                    error_message=error,
                )
                if error is not None
                else failure
                for _, _, error in prepared
            ]

        bulk: BulkCreateResult = result.value
        by_invoice = bulk.by_invoice()

        results: list[BookingResult] = []
        for _req, payload, error in prepared:
            if error is not None or payload is None:
                results.append(
                    BookingResult(
                        outcome=BookingOutcome.FAILED,
                        error_code=str(SteadfastErrorKind.VALIDATION),
                        error_message=error,
                    )
                )
                continue

            item = by_invoice.get(payload.invoice)
            if item is None:
                # We sent it and the response does not mention it. The parcel
                # may or may not exist: exactly the ambiguous case.
                results.append(
                    BookingResult(
                        outcome=BookingOutcome.UNKNOWN,
                        error_code=str(SteadfastErrorKind.AMBIGUOUS),
                        error_message=("The courier's bulk response did not mention this order"),
                        provider_request_id=result.correlation_id,
                    )
                )
                continue

            if item.succeeded:
                results.append(
                    BookingResult(
                        outcome=BookingOutcome.BOOKED,
                        consignment=ProviderConsignment(
                            provider_consignment_id=item.consignment_id or "",
                            tracking_code=item.tracking_code,
                            merchant_reference=payload.invoice,
                            raw=item.raw,
                        ),
                        provider_request_id=result.correlation_id,
                    )
                )
            else:
                # A per-item "error" is the provider answering about this
                # specific item. That is a determinate refusal, not ambiguity —
                # and one failed item must not disturb the successful ones.
                results.append(
                    BookingResult(
                        outcome=BookingOutcome.FAILED,
                        error_code=str(SteadfastErrorKind.VALIDATION),
                        error_message="The courier rejected this order",
                        provider_request_id=result.correlation_id,
                    )
                )
        return results

    # --------------------------------------------------------------- status --

    async def get_status(self, creds: Any, reference: str) -> ProviderStatus | Unavailable:
        """Normalized status lookup by the provider's consignment id."""
        return await self.get_status_by(creds, reference, kind=StatusLookupKind.CONSIGNMENT_ID)

    async def get_status_by(
        self, creds: Any, reference: str, *, kind: StatusLookupKind
    ) -> ProviderStatus | Unavailable:
        """The specific provider form, retained internally (brief section 13)."""
        credentials = _as_credentials(creds)
        result = await self._client.get_status(credentials, reference, kind=kind)
        response: StatusResponse = result.value
        mapping = map_delivery_status(response.delivery_status)
        return ProviderStatus(
            provider_consignment_id=reference,
            raw_status=response.delivery_status,
            normalized_status=str(mapping.canonical) if mapping.canonical else None,
            # The document gives no status timestamp. Inventing `now()` would
            # make an observation look like an event.
            updated_at=None,
            raw=response.raw,
        )

    # -------------------------------------------------------------- returns --

    async def request_return(self, creds: Any, reference: str, reason: str) -> bool | Unavailable:
        """Ask Steadfast to return a parcel, by its consignment id.

        The richer path — choosing which of the three documented references to
        send, and recording the provider's return-request record — is
        :meth:`create_return_request`. This method exists to satisfy the
        generic adapter protocol.
        """
        record = await self.create_return_request(creds, consignment_id=reference, reason=reason)
        return isinstance(record, ReturnRequestRecord)

    async def create_return_request(
        self,
        creds: Any,
        *,
        consignment_id: str | None = None,
        invoice: str | None = None,
        tracking_code: str | None = None,
        reason: str | None = None,
    ) -> ReturnRequestRecord:
        credentials = _as_credentials(creds)
        result = await self._client.create_return_request(
            credentials,
            consignment_id=consignment_id,
            invoice=invoice,
            tracking_code=tracking_code,
            reason=reason,
        )
        record: ReturnRequestRecord = result.value
        return record

    # -------------------------------------------------------------- balance --

    async def get_balance(self, creds: Any) -> Money | Unavailable:
        """The provider's own account balance.

        Never an ecomsbd COD receivable. The document calls it "current
        balance" and says nothing more; it is reported as the provider's
        number, labelled as such (brief section 19).
        """
        credentials = _as_credentials(creds)
        result = await self._client.get_balance(credentials)
        return Money(paisa=int(result.value), currency=BDT)

    async def balance_call(self, creds: Any) -> SteadfastCallResult:
        """Balance plus its evidence trail, for the observation record."""
        return await self._client.get_balance(_as_credentials(creds))

    # ------------------------------------------------- documented as absent --

    async def list_stores(self, creds: Any) -> list[Store] | Unavailable:
        return Unavailable(Capability.LIST_STORES, _UNSUPPORTED_REASONS[Capability.LIST_STORES])

    async def quote(self, creds: Any, req: BookingRequest) -> Quote | Unavailable:
        return Unavailable(Capability.PRICE_QUOTE, _UNSUPPORTED_REASONS[Capability.PRICE_QUOTE])

    async def cancel(self, creds: Any, reference: str) -> bool | Unavailable:
        return Unavailable(Capability.CANCEL, _UNSUPPORTED_REASONS[Capability.CANCEL])

    async def customer_stats(
        self, creds: Any, phone_e164: str
    ) -> ProviderCustomerStats | Unavailable:
        return Unavailable(
            Capability.CUSTOMER_STATS, _UNSUPPORTED_REASONS[Capability.CUSTOMER_STATS]
        )

    async def list_payouts(self, creds: Any, since: Any) -> list[ProviderPayout] | Unavailable:
        """Not this interface.

        Steadfast's payments endpoints have no documented response schema, so
        turning them into ``ProviderPayout`` objects behind a generic method
        would hide how much of the result is inferred. Payment ingestion goes
        through :mod:`app.couriers.payments`, which keeps the raw record, the
        inference and the reconciliation decision visible and separate.
        """
        return Unavailable(
            Capability.PAYOUTS,
            "Steadfast payments are synced through the payments endpoints, "
            "not through a payout statement API.",
        )

    # ------------------------------------------------------------- webhooks --

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        """Always ``False``. There is no documented webhook contract.

        Returning ``True`` — or inventing an HMAC over a guessed header — would
        mean accepting unauthenticated requests that move money. The webhook
        route exists and is wired; it refuses until a real contract is supplied
        (``STEADFAST_WEBHOOK_CONTRACT_REQUIRED``).
        """
        return False

    def parse_webhook(self, headers: dict[str, str], body: bytes) -> list[ProviderEvent]:
        """Always empty. No payload shape is documented, so none is parsed."""
        return []


_UNVERIFIABLE_MESSAGE = (
    "Steadfast did not answer, so these credentials could not be checked. "
    "They have not been marked wrong — try again shortly."
)


def _booking_failure(exc: SteadfastError) -> BookingResult:
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


def _as_credentials(creds: Any) -> SteadfastCredentials:
    if isinstance(creds, SteadfastCredentials):
        return creds
    if isinstance(creds, dict):
        return SteadfastCredentials(
            api_key=str(creds["api_key"]), secret_key=str(creds["secret_key"])
        )
    raise TypeError("Steadfast needs SteadfastCredentials or a dict with api_key and secret_key")
