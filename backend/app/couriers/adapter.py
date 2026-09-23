"""The courier adapter contract.

Master spec section 34 defines the protocol; section 35 defines the shared
wrapper (credential decrypt, token cache, distributed lock, timeouts, retry
policy, circuit breaker, rate limiting, redacted logs, provider health).

Two rules from the spec are encoded as types here, not as documentation:

*   **An unsupported capability returns "unavailable", it does not raise.** A
    provider without a payout API must not break the money screen; it must show
    "upload a statement" (section 75). :class:`Unavailable` is that answer.
*   **An ambiguous create is never retried.** :class:`BookingOutcome` has a
    distinct ``UNKNOWN`` member, and the domain maps it to ``BOOKING_UNKNOWN``
    for reconciliation against the merchant reference (sections 11, 36). A
    timeout after a courier create may mean the parcel *was* created; retrying
    it ships two parcels and pays for both.

No provider HTTP calls exist yet. Implementing one requires verified merchant
documentation (sections 61, 140), and this file deliberately contains no
endpoint, header, signature or status-string guesses.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from app.common.money import Money
from app.couriers.capabilities import Capability

__all__ = [
    "BookingOutcome",
    "BookingPreview",
    "BookingRequest",
    "BookingResult",
    "CourierAdapter",
    "ProviderConsignment",
    "ProviderCustomerStats",
    "ProviderEvent",
    "ProviderPayout",
    "ProviderPayoutLine",
    "ProviderStatus",
    "Quote",
    "Store",
    "Unavailable",
    "ValidationResult",
]


class Unavailable:
    """Returned when a provider does not support a capability.

    Falsy, so ``if not result:`` reads naturally, and carries the reason so the
    UI can explain *why* rather than showing an empty state.
    """

    __slots__ = ("capability", "reason")

    def __init__(self, capability: Capability, reason: str = "") -> None:
        self.capability = capability
        self.reason = reason or f"{capability} is not available for this provider"

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return f"Unavailable({self.capability}, {self.reason!r})"


class BookingOutcome(StrEnum):
    """Result of a create-consignment call.

    ``UNKNOWN`` is the whole point of this enum. Collapsing it into ``FAILED``
    is the duplicate-booking bug (master spec sections 11, 36, 117).
    """

    BOOKED = "BOOKED"
    #: The provider positively rejected the request (validation, bad address).
    FAILED = "FAILED"
    #: Timeout or unreadable response. The parcel may or may not exist.
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Outcome of validating merchant credentials before saving them.

    ``valid`` and ``rejected`` are deliberately two fields rather than one
    tri-state, because the third case is the one that matters and it is easy to
    lose. ``valid=False, rejected=True`` means the provider said no. ``valid=
    False, rejected=False`` means we never found out — a timeout, a 500, an
    unreadable body — and **must not** count against the account. A seller
    re-typing a working API key because the provider had a bad minute is the
    failure this shape exists to prevent (master spec section 49; brief
    section 5).
    """

    valid: bool
    message: str | None = None
    #: Capabilities the provider confirmed for this specific account.
    detected_capabilities: frozenset[Capability] = field(default_factory=frozenset)
    account_label: str | None = None
    #: Whether the provider positively refused these credentials. Only ever
    #: ``True`` for a deterministic rejection the provider actually answered
    #: with; never inferred from a transport failure.
    rejected: bool = False
    #: The provider's own account balance, when the call that validated the
    #: credentials read it anyway. Stored as the provider's figure only.
    reported_balance_paisa: int | None = None

    @property
    def is_inconclusive(self) -> bool:
        """We asked and did not get an answer about the credentials."""
        return not self.valid and not self.rejected


@dataclass(frozen=True, slots=True)
class Store:
    """A pickup location on a merchant account (master spec section 73)."""

    provider_store_id: str
    name: str
    address: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Quote:
    """A live delivery charge quote, when the provider offers one."""

    delivery_fee: Money
    cod_fee: Money | None = None
    return_fee: Money | None = None
    currency: str = "BDT"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BookingRequest:
    """Normalized booking input. Provider field mapping happens in the adapter."""

    order_id: uuid.UUID
    #: Stable merchant reference. The key to resolving a BOOKING_UNKNOWN.
    merchant_reference: str
    recipient_name: str
    recipient_phone_e164: str
    #: Full free-text address; providers with auto-address use this directly
    #: (master spec section 12).
    recipient_address: str
    cod_amount: Money
    item_description: str
    item_quantity: int = 1
    weight_grams: int | None = None
    note: str | None = None
    store_reference: str | None = None
    recipient_alternate_phone_e164: str | None = None
    #: A delivery area the seller picked from the provider's own area list,
    #: for providers whose create requires one (RedX). Both halves travel
    #: together: RedX takes the area's name *and* its id on every create.
    delivery_area_id: str | None = None
    delivery_area_name: str | None = None
    #: The value of the goods, for providers that ask for a declared value
    #: (RedX's ``value``, used for compensation). ``None`` when not known.
    declared_value: Money | None = None


@dataclass(frozen=True, slots=True)
class BookingPreview:
    """What a provider is about to send, in a form safe to persist.

    This exists so the booking service can record evidence and mask a phone
    number **without knowing which provider it is talking to**. Before it, the
    generic service called Steadfast's payload builder for every provider,
    which meant a Pathao booking stored a Steadfast-shaped request in
    ``courier_raw_payloads`` — evidence that describes a call nobody made, in
    the one table whose entire purpose is explaining a result months later.

    Building it is also the provider's local validation step: an adapter raises
    ``ValueError`` from :meth:`CourierAdapter.describe_booking` when it would
    refuse the booking itself, before anything is persisted as sent.
    """

    #: The provider-shaped request body, already masked. Stored as evidence, so
    #: it must be safe at rest rather than relying on a log redactor.
    redacted_payload: dict[str, Any]
    #: The recipient phone in the provider's own format, masked.
    recipient_phone_masked: str
    #: Whole taka the courier is asked to collect.
    cod_taka: int
    #: The paisa remainder that cannot be handed over in cash. Recorded, never
    #: dropped, so a receivable is never quietly a few paisa from the money
    #: that can actually arrive.
    cod_residual_paisa: int


@dataclass(frozen=True, slots=True)
class ProviderConsignment:
    """A parcel as the provider reports it."""

    provider_consignment_id: str
    tracking_code: str | None
    merchant_reference: str
    #: The provider's own status string, preserved verbatim (section 62.13).
    raw_status: str | None = None
    charge: Money | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BookingResult:
    """What a create call produced, including the ambiguous case."""

    outcome: BookingOutcome
    consignment: ProviderConsignment | None = None
    error_code: str | None = None
    error_message: str | None = None
    #: Provider-side request id, for support traces.
    provider_request_id: str | None = None

    @property
    def is_ambiguous(self) -> bool:
        return self.outcome is BookingOutcome.UNKNOWN


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    """A status lookup response."""

    provider_consignment_id: str
    raw_status: str
    #: Domain status is derived by the adapter's mapping table, never guessed
    #: in shared code.
    normalized_status: str | None = None
    updated_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderEvent:
    """A normalized webhook or polling event."""

    provider_consignment_id: str
    raw_status: str
    occurred_at: datetime | None
    #: Provider event id, the preferred deduplication key (section 78).
    provider_event_id: str | None = None
    normalized_status: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderPayoutLine:
    """One line of a provider payout."""

    provider_consignment_id: str | None
    tracking_code: str | None
    merchant_reference: str | None
    amount: Money
    #: Provider's own label for a deduction; never coerced into a known charge
    #: type when it is not recognised (master spec section 84).
    adjustment_label: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderPayout:
    """A settlement as the provider reports it."""

    provider_payout_id: str
    total_amount: Money
    paid_at: datetime | None
    business_date: date | None
    lines: list[ProviderPayoutLine] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderCustomerStats:
    """Delivery-history totals for a phone number, used by the risk check.

    Deliberately totals only. Master spec section 130 forbids person-level
    labels; this feeds an *order risk* signal, not a verdict about a person.
    """

    total_parcels: int
    successful: int
    cancelled_or_returned: int
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class CourierAdapter(Protocol):
    """Every courier integration implements this and nothing else.

    Core order, money and profit code must never branch on a provider name;
    provider-specific mapping lives behind this interface (master spec
    sections 34, 62.3).
    """

    provider: str

    #: ecomsbd's batch size for this provider. A property rather than a shared
    #: setting because each provider's ceiling — and each provider's blast
    #: radius when a batch fails — is its own.
    @property
    def bulk_chunk_size(self) -> int: ...

    def describe_booking(self, req: BookingRequest, merchant_reference: str) -> BookingPreview:
        """What this adapter would send, safe to persist, plus the COD split.

        Raises ``ValueError`` when the provider would refuse the booking
        locally — a phone that is not a Bangladeshi mobile, an address below a
        documented minimum, a reference outside the allowed character set. The
        booking service calls this *before* anything is recorded as sent, so a
        locally-invalid booking never produces an attempt row or a provider
        call.
        """
        ...

    async def validate_credentials(self, creds: Any) -> ValidationResult: ...

    async def capabilities(self, creds: Any) -> set[Capability]: ...

    async def list_stores(self, creds: Any) -> list[Store] | Unavailable: ...

    async def quote(self, creds: Any, req: BookingRequest) -> Quote | Unavailable: ...

    async def create_consignment(
        self, creds: Any, req: BookingRequest, merchant_reference: str
    ) -> BookingResult: ...

    async def create_bulk(
        self, creds: Any, reqs: list[BookingRequest]
    ) -> list[BookingResult] | Unavailable: ...

    async def get_status(self, creds: Any, reference: str) -> ProviderStatus | Unavailable: ...

    async def cancel(self, creds: Any, reference: str) -> bool | Unavailable: ...

    async def request_return(
        self, creds: Any, reference: str, reason: str
    ) -> bool | Unavailable: ...

    async def get_balance(self, creds: Any) -> Money | Unavailable: ...

    async def list_payouts(
        self, creds: Any, since: datetime
    ) -> list[ProviderPayout] | Unavailable: ...

    async def customer_stats(
        self, creds: Any, phone_e164: str
    ) -> ProviderCustomerStats | Unavailable: ...

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool: ...

    def parse_webhook(self, headers: dict[str, str], body: bytes) -> list[ProviderEvent]: ...
