"""The billing provider contract.

Master spec section 92 defines the interface; sections 90 and 91 define what an
implementation is allowed to claim. Two rules are enforced by the shape of these
types rather than by documentation:

*   **A provider reports whether it can work.** :meth:`BillingProvider.availability`
    returns a blocker code, not a boolean, so an unconfigured provider surfaces
    ``PLAY_BILLING_EXTERNAL_CONFIGURATION_REQUIRED`` to the operator instead of
    silently behaving like a configured one that keeps saying no.
*   **Verification returns evidence, not a verdict alone.**
    :class:`VerifiedPurchase` carries the provider's own state string, product
    id and expiry. The service decides what to grant from those, so a provider
    cannot grant entitlement by returning ``True``.

Nothing here performs an HTTP call. Each provider takes a *transport port* —
:class:`~app.billing.providers.google_play.PlayApiClient`,
:class:`~app.billing.providers.bkash_web.BkashApiClient` — and this repository
ships no live implementation of either, because neither the Play service account
and package name nor the bKash merchant contract exist yet. Everything on this
side of the port is real, tested and ready for those credentials.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from app.billing.models import BillingEventType, BillingProviderKind
from app.core.errors import AppError, ErrorCode
from app.entitlements.catalog import PlanCode

__all__ = [
    "BillingEvent",
    "BillingProvider",
    "CancelResult",
    "CheckoutSession",
    "ProviderAvailability",
    "ProviderBlocker",
    "ProviderNotConfiguredError",
    "RefundResult",
    "SubscriptionState",
    "VerificationResult",
    "VerifiedPurchase",
]


class ProviderBlocker(StrEnum):
    """Why a provider cannot operate. These strings are release blockers.

    They appear verbatim in ``docs/RELEASE_READINESS.md`` and in the admin
    console, so an operator reading either sees the same identifier.
    """

    NONE = "NONE"
    PLAY_BILLING_EXTERNAL_CONFIGURATION_REQUIRED = "PLAY_BILLING_EXTERNAL_CONFIGURATION_REQUIRED"
    PACKAGE_ID_DECISION_REQUIRED = "PACKAGE_ID_DECISION_REQUIRED"
    BKASH_MERCHANT_SETUP_REQUIRED = "BKASH_MERCHANT_SETUP_REQUIRED"
    FEATURE_FLAG_DISABLED = "FEATURE_FLAG_DISABLED"
    #: The provider is configured but has no live transport wired in. This is
    #: the state a correctly-built-but-not-yet-credentialed integration reports.
    TRANSPORT_NOT_IMPLEMENTED = "TRANSPORT_NOT_IMPLEMENTED"


class VerificationResult(StrEnum):
    """Outcome of asking a provider about a purchase.

    Only ``VERIFIED`` may grant entitlement. Every other value is recorded on
    the billing transaction so support can answer "why did my payment not
    unlock Pro?" without guessing.
    """

    VERIFIED = "VERIFIED"
    #: The provider says this purchase is not (or no longer) valid.
    REJECTED = "REJECTED"
    #: We have seen this token before, bound to someone else.
    REPLAY = "REPLAY"
    #: Token belongs to a different tenant.
    WRONG_TENANT = "WRONG_TENANT"
    #: Product id is not one we sell, or maps to no plan.
    UNKNOWN_PRODUCT = "UNKNOWN_PRODUCT"
    #: Package name does not match this build.
    WRONG_PACKAGE = "WRONG_PACKAGE"
    #: We could not reach the provider. Distinct from REJECTED: the purchase
    #: may be perfectly good and must be retried, never refused permanently.
    UNAVAILABLE = "UNAVAILABLE"
    #: The provider is not configured in this deployment.
    NOT_CONFIGURED = "NOT_CONFIGURED"


class ProviderNotConfiguredError(AppError):
    """Raised when a billing operation needs configuration that does not exist.

    Presented as 503 rather than 500: it is an operator-fixable deployment gap,
    not a bug, and the client should show "not available right now" rather than
    "something went wrong".
    """

    code = ErrorCode.SERVICE_UNAVAILABLE

    def __init__(self, provider: str, blocker: ProviderBlocker, message: str | None = None) -> None:
        super().__init__(
            message or f"{provider} billing is not configured in this deployment",
            details={"provider": provider, "blocker": str(blocker)},
        )
        self.provider = provider
        self.blocker = blocker


@dataclass(frozen=True, slots=True)
class ProviderAvailability:
    """Whether a provider can be used, and if not, what is missing."""

    provider: BillingProviderKind
    available: bool
    blocker: ProviderBlocker = ProviderBlocker.NONE
    #: Operator-facing sentence. Never shown to a seller.
    detail: str | None = None
    #: Whether this provider may be *offered* in the current distribution
    #: channel, independent of whether it is configured (section 27.1).
    allowed_in_channel: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": str(self.provider),
            "available": self.available,
            "blocker": str(self.blocker),
            "detail": self.detail,
            "allowed_in_channel": self.allowed_in_channel,
        }


@dataclass(frozen=True, slots=True)
class CheckoutSession:
    """A started purchase the seller has to complete somewhere else."""

    provider: BillingProviderKind
    checkout_reference: str
    plan: PlanCode
    amount_paisa: int
    currency: str = "BDT"
    #: Where to send the seller. ``None`` for Play, where the purchase happens
    #: inside the app through the platform billing library.
    redirect_url: str | None = None
    expires_at: datetime | None = None
    #: Provider-neutral instructions for the client, e.g. which in-app product
    #: id to launch. Never contains a secret.
    client_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VerifiedPurchase:
    """What the provider said about a purchase.

    ``result`` is the verdict; everything else is the evidence behind it. The
    service writes all of it to ``billing_transactions``, so a later dispute is
    answered from our own records rather than by asking the provider again.
    """

    provider: BillingProviderKind
    result: VerificationResult
    plan: PlanCode | None = None
    provider_reference: str | None = None
    provider_event_id: str | None = None
    provider_product_id: str | None = None
    #: The provider's own state string, unmapped.
    purchase_state: str | None = None
    amount_paisa: int = 0
    currency: str = "BDT"
    expiry_at: datetime | None = None
    starts_at: datetime | None = None
    auto_renewing: bool = False
    acknowledgement_required: bool = False
    #: Obfuscated buyer/customer id, for binding the purchase to a tenant.
    account_reference: str | None = None
    detail: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def verified(self) -> bool:
        return self.result is VerificationResult.VERIFIED


@dataclass(frozen=True, slots=True)
class SubscriptionState:
    """Provider truth about a subscription, used by the reconciliation job."""

    provider: BillingProviderKind
    known: bool
    active: bool = False
    plan: PlanCode | None = None
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    auto_renewing: bool = False
    cancelled: bool = False
    in_provider_grace: bool = False
    purchase_state: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class CancelResult:
    """Outcome of asking a provider to cancel."""

    provider: BillingProviderKind
    accepted: bool
    #: True when access legitimately continues to the end of the paid period.
    effective_at_period_end: bool = True
    effective_at: datetime | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class RefundResult:
    provider: BillingProviderKind
    accepted: bool
    amount_paisa: int = 0
    provider_reference: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class BillingEvent:
    """One provider-neutral lifecycle event parsed out of a webhook.

    ``dedupe_key`` and ``dedupe_source`` implement the section 78 priority:
    provider event id, else provider reference, else a payload fingerprint. The
    source is carried so a support engineer can see how strong the dedupe was —
    a fingerprint match is a weaker statement than an event-id match.
    """

    provider: BillingProviderKind
    event_type: BillingEventType
    dedupe_key: str
    dedupe_source: str
    occurred_at: datetime | None = None
    provider_event_id: str | None = None
    provider_reference: str | None = None
    provider_product_id: str | None = None
    plan: PlanCode | None = None
    #: Purchase token hash, when the payload carries one. Used to find the
    #: tenant without ever storing the token itself.
    purchase_token_hash: str | None = None
    tenant_id: uuid.UUID | None = None
    amount_paisa: int = 0
    currency: str = "BDT"
    failure_code: str | None = None
    expiry_at: datetime | None = None
    #: Already redacted by the provider implementation.
    payload: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class BillingProvider(Protocol):
    """Master spec section 92.

    Every method is allowed to raise :class:`ProviderNotConfiguredError`. None
    of them may invent a successful result when the provider cannot be reached:
    the correct answer to "is this purchase real?" with no way to ask is
    :attr:`VerificationResult.UNAVAILABLE`, never ``VERIFIED``.
    """

    kind: BillingProviderKind

    def availability(self) -> ProviderAvailability: ...

    async def create_checkout(
        self, *, tenant_id: uuid.UUID, plan: PlanCode, reference: str
    ) -> CheckoutSession: ...

    async def verify_purchase(self, payload: dict[str, Any]) -> VerifiedPurchase: ...

    async def cancel_subscription(
        self, *, provider_reference: str, at_period_end: bool
    ) -> CancelResult: ...

    async def sync_subscription(self, *, provider_reference: str) -> SubscriptionState: ...

    async def refund(
        self, *, provider_reference: str, amount_paisa: int
    ) -> RefundResult | None: ...

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool: ...

    def parse_webhook(self, body: bytes) -> list[BillingEvent]: ...
