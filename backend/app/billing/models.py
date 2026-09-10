"""Billing tables.

Master spec section 93 names the tables; sections 90 and 91 define what has to
be true about them. Three rules shape every model here:

*   **Every billing change is traceable.** A subscription's state is a
    derived, mutable summary; the immutable record of *why* it holds that state
    is :class:`BillingTransaction` plus :class:`SubscriptionEvent`. Losing the
    summary is recoverable, losing the history is not.
*   **No payment secret is stored.** A Play purchase token and a bKash payment
    reference are bearer credentials: whoever holds one can act on the purchase.
    They are kept as a SHA-256 hash, which is enough to recognise a replay and
    useless to anyone who reads the table.
*   **Replay protection is global, not per tenant.** :class:`PlayPurchaseToken`
    and :class:`BillingWebhookEvent` are deliberately *not*
    :class:`~app.db.base.TenantOwned`: a token presented by the wrong tenant has
    to be *found* and rejected, and a tenant-filtered read would report it as
    unseen and then grant a second entitlement from one purchase.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utc_now
from app.core.ids import new_id
from app.db.base import Base, PrimaryKeyMixin, TenantOwned, TimestampMixin
from app.db.types import GUID, JSONColumn, Paisa, TZDateTime

__all__ = [
    "BillingAttempt",
    "BillingAttemptState",
    "BillingEventType",
    "BillingProviderCustomer",
    "BillingProviderKind",
    "BillingTransaction",
    "BillingWebhookEvent",
    "DistributionChannel",
    "PlayPurchaseToken",
    "SubscriptionEvent",
    "TransactionKind",
    "TransactionState",
    "WebhookProcessingState",
    "hash_provider_token",
]


def hash_provider_token(token: str) -> str:
    """SHA-256 of a provider token or reference.

    The raw value never reaches the database. Recognising "we have seen this
    token before" needs only a stable fingerprint, and a hash cannot be replayed
    against the provider if the table leaks.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class BillingProviderKind(StrEnum):
    """Who is charging the seller (master spec section 92)."""

    PLAY = "play"
    BKASH_WEB = "bkash_web"
    #: Support credit, plan extension, pilot access. Never self-service.
    MANUAL_ADMIN = "manual_admin"


class DistributionChannel(StrEnum):
    """How this installation reached the seller (master spec section 27.1).

    The billing call-to-action derives from this plus provider availability plus
    entitlement — never from a platform check scattered through a widget. A Play
    build must not offer an external payment link where Play policy forbids
    steering, and that decision is made here, once, on the server.
    """

    PLAY = "PLAY"
    WEB = "WEB"
    DIRECT = "DIRECT"
    INTERNAL_TEST = "INTERNAL_TEST"

    @property
    def allows_external_payment_cta(self) -> bool:
        """Whether an out-of-app payment link may be shown in this build.

        Section 27.1: Bangladesh is not in the alternative-billing country list
        in the sources this specification was verified against, so a Play build
        gets ``False``. Re-check the policy immediately before release.
        """
        return self is not DistributionChannel.PLAY


class TransactionKind(StrEnum):
    """What a billing transaction represents."""

    PURCHASE = "PURCHASE"
    RENEWAL = "RENEWAL"
    REFUND = "REFUND"
    CHARGEBACK = "CHARGEBACK"
    #: Support-issued plan time. Carries no money.
    CREDIT = "CREDIT"


class TransactionState(StrEnum):
    """Where a transaction stands.

    ``PENDING`` means a client claimed something and we have not confirmed it.
    Only ``VERIFIED`` grants entitlement.
    """

    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"


class BillingEventType(StrEnum):
    """Provider-neutral subscription lifecycle events (master spec section 16 of
    the Phase F brief, generalised from sections 90–91).

    These are *domain* events. They are deliberately not named after any
    provider's notification types, because no provider's schedule or naming has
    been verified against merchant documentation yet.
    """

    SUBSCRIPTION_STARTED = "SUBSCRIPTION_STARTED"
    SUBSCRIPTION_RENEWED = "SUBSCRIPTION_RENEWED"
    SUBSCRIPTION_CANCELLED = "SUBSCRIPTION_CANCELLED"
    SUBSCRIPTION_EXPIRED = "SUBSCRIPTION_EXPIRED"
    SUBSCRIPTION_RESTORED = "SUBSCRIPTION_RESTORED"
    SUBSCRIPTION_REVOKED = "SUBSCRIPTION_REVOKED"
    PLAN_CHANGED = "PLAN_CHANGED"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    PAYMENT_RETRY_PENDING = "PAYMENT_RETRY_PENDING"
    PAYMENT_RECOVERED = "PAYMENT_RECOVERED"
    GRACE_STARTED = "GRACE_STARTED"
    GRACE_ENDED = "GRACE_ENDED"
    REFUNDED = "REFUNDED"
    SUSPENDED = "SUSPENDED"
    #: A reconciliation run found provider truth differing from local state.
    RECONCILED = "RECONCILED"


class BillingAttemptState(StrEnum):
    """Dunning attempt state (master spec section 91: model dunning generically)."""

    PENDING = "PENDING"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    FAILED = "FAILED"
    RECOVERED = "RECOVERED"
    ABANDONED = "ABANDONED"


class WebhookProcessingState(StrEnum):
    RECEIVED = "RECEIVED"
    PROCESSED = "PROCESSED"
    #: Signature or payload rejected. Kept so a support engineer can see it
    #: arrived, without the payload ever being trusted.
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    #: Seen before. Recorded on the original row's attempt counter, not as a
    #: second row.
    DUPLICATE = "DUPLICATE"


# --------------------------------------------------------------------------- #
# Transactions and events
# --------------------------------------------------------------------------- #


class BillingTransaction(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """One money-or-entitlement movement, verified or refused.

    Immutable in practice: the service appends rows and never rewrites one. A
    refund is a new ``REFUND`` row pointing at the original, the same shape the
    financial ledger uses in section 80.
    """

    __tablename__ = "billing_transactions"
    __table_args__ = (
        # Provider event ids are globally unique per provider, so the constraint
        # is not tenant-scoped: the same Play order id arriving for two tenants
        # is an attack or a bug, not two legitimate purchases.
        sa.UniqueConstraint(
            "provider",
            "provider_event_id",
            name="uq_billing_transactions_provider_provider_event_id",
        ),
        sa.Index("ix_billing_transactions_tenant_occurred", "tenant_id", "occurred_at"),
        sa.Index("ix_billing_transactions_reference", "provider", "provider_reference"),
        sa.CheckConstraint("amount_paisa >= 0", name="amount_non_negative"),
    )

    subscription_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True, index=True)

    provider: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    #: Provider's own identifier for this event. NULL only for manual credits,
    #: which have no provider side.
    provider_event_id: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    #: The purchase/subscription this event belongs to.
    provider_reference: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)

    plan_code: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    provider_product_id: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)

    kind: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    state: Mapped[str] = mapped_column(sa.String(20), nullable=False)

    amount_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)
    #: ISO 4217. BDT for bKash; Play may settle in another currency, and
    #: pretending otherwise would corrupt any later revenue report.
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default="BDT")

    #: Why we believe (or refuse to believe) this transaction. A short machine
    #: string plus a human sentence — never the provider's raw response, which
    #: can carry tokens.
    verification_result: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    verification_detail: Mapped[str | None] = mapped_column(sa.String(400), nullable=True)

    #: Redacted provider payload: safe fields only, written through
    #: :func:`app.core.redaction.redact_value`.
    metadata_json: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)

    #: When it happened at the provider, versus when we heard about it. The gap
    #: is the webhook lag metric in section 49.
    occurred_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utc_now)
    received_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utc_now)


class SubscriptionEvent(Base, TenantOwned, PrimaryKeyMixin):
    """An append-only transition log for one subscription.

    Section 93 requires the subscription's state to be explainable. A row here
    answers "why is this shop on GRACE?" without needing the provider.
    """

    __tablename__ = "subscription_events"
    __table_args__ = (
        sa.Index("ix_subscription_events_subscription", "subscription_id", "created_at"),
        sa.Index("ix_subscription_events_tenant_created", "tenant_id", "created_at"),
    )

    subscription_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False)
    event_type: Mapped[str] = mapped_column(sa.String(40), nullable=False)

    from_status: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    to_status: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    from_plan: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    to_plan: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)

    provider: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    provider_event_id: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)

    #: Free text for support: "card declined", "seller cancelled in Play".
    reason: Mapped[str | None] = mapped_column(sa.String(400), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)

    occurred_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utc_now)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utc_now, index=True
    )


class BillingProviderCustomer(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """The tenant's identity at a billing provider.

    One row per (tenant, provider). Play identifies a buyer by an obfuscated
    account id; bKash by a customer/agreement reference. Both are stored hashed
    plus a display suffix, because neither is needed in clear text to do the job
    and both identify a person.
    """

    __tablename__ = "billing_provider_customers"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id", "provider", name="uq_billing_provider_customers_tenant_id_provider"
        ),
        sa.Index("ix_billing_provider_customers_reference_hash", "provider", "reference_hash"),
    )

    provider: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    reference_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    #: Last four characters, for support to confirm "the account ending 4f21".
    reference_suffix: Mapped[str | None] = mapped_column(sa.String(8), nullable=True)

    #: Which user linked it, so a shop with a team can see who owns the billing
    #: relationship.
    linked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    linked_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utc_now)
    metadata_json: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)


class PlayPurchaseToken(Base, PrimaryKeyMixin, TimestampMixin):
    """A Google Play purchase token we have seen.

    **Platform-scoped on purpose.** A purchase token identifies one purchase in
    the world. If this table were tenant-filtered, tenant B presenting tenant
    A's token would read "no such token", pass the replay check, and receive a
    second entitlement from a single payment. So the row is found globally and
    the binding is compared explicitly — see
    :meth:`app.billing.service.BillingService.verify_play_purchase`.

    The token itself is never stored: :func:`hash_provider_token` is the key.
    """

    __tablename__ = "play_purchase_tokens"
    __table_args__ = (
        sa.UniqueConstraint("token_hash", name="uq_play_purchase_tokens_token_hash"),
        sa.Index("ix_play_purchase_tokens_tenant", "tenant_id"),
        sa.Index("ix_play_purchase_tokens_order", "provider_order_id"),
    )

    token_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)

    #: The tenant this purchase is bound to. A plain column rather than the
    #: TenantOwned mixin, so the replay lookup is not tenant-filtered.
    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)

    package_name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    product_id: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    plan_code: Mapped[str] = mapped_column(sa.String(32), nullable=False)

    #: Provider-reported purchase state, kept as the provider's own string so a
    #: value we have not modelled is preserved rather than coerced.
    purchase_state: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)
    acknowledged: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    provider_order_id: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    expiry_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    auto_renewing: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)

    first_verified_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utc_now)
    last_verified_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utc_now)
    #: How many times this token has been presented. A climbing count on a
    #: single token is the signal a replay is being attempted.
    presentation_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)

    revoked_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)


class BillingAttempt(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """One payment attempt in a dunning cycle (master spec section 91).

    The retry *schedule* is configuration, not a constant: neither Play's nor
    bKash's real retry behaviour has been verified against merchant
    documentation, and hard-coding a guess would make the seller-facing "we will
    try again on the 14th" a lie.
    """

    __tablename__ = "billing_attempts"
    __table_args__ = (
        sa.Index("ix_billing_attempts_subscription", "subscription_id", "created_at"),
        sa.Index("ix_billing_attempts_next_retry", "state", "next_retry_at"),
        sa.CheckConstraint("attempt_number >= 1", name="attempt_number_positive"),
    )

    subscription_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False)
    provider: Mapped[str] = mapped_column(sa.String(32), nullable=False)

    attempt_number: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    state: Mapped[str] = mapped_column(sa.String(20), nullable=False)

    #: Provider's failure code, kept verbatim. "INSUFFICIENT_FUNDS" from a
    #: provider means something specific; mapping it to our own vocabulary
    #: loses the only detail support can act on.
    failure_code: Mapped[str | None] = mapped_column(sa.String(80), nullable=True)
    failure_detail: Mapped[str | None] = mapped_column(sa.String(400), nullable=True)

    amount_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default="BDT")

    occurred_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utc_now)
    next_retry_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)


class BillingWebhookEvent(Base, PrimaryKeyMixin):
    """A received billing webhook, deduplicated before it is trusted.

    **Platform-scoped**, like :class:`PlayPurchaseToken`: a webhook arrives
    unauthenticated and before any tenant is known. ``tenant_id`` is filled in
    once the payload resolves to a subscription, and stays NULL when it does
    not — which is itself a support signal.

    Dedupe follows the section 78 priority the courier webhooks already use:
    provider event id, then provider reference, then a payload fingerprint.
    """

    __tablename__ = "billing_webhook_events"
    __table_args__ = (
        sa.UniqueConstraint(
            "provider", "dedupe_key", name="uq_billing_webhook_events_provider_dedupe_key"
        ),
        sa.Index("ix_billing_webhook_events_received", "received_at"),
        sa.Index("ix_billing_webhook_events_state", "state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_id)

    provider: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    #: Whichever of the three dedupe signals was available, prefixed with which
    #: one it is (``event:``, ``ref:``, ``fingerprint:``) so a support engineer
    #: can see how confident the deduplication was.
    dedupe_key: Mapped[str] = mapped_column(sa.String(220), nullable=False)
    dedupe_source: Mapped[str] = mapped_column(sa.String(20), nullable=False)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True, index=True)
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)

    state: Mapped[str] = mapped_column(
        sa.String(20), nullable=False, default=WebhookProcessingState.RECEIVED
    )
    event_type: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)
    signature_verified: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)

    delivery_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    error: Mapped[str | None] = mapped_column(sa.String(400), nullable=True)

    #: Redacted. Never the raw body: a Play RTDN or a bKash IPN can carry a
    #: purchase token, which is a bearer credential.
    payload: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)
    payload_sha256: Mapped[str] = mapped_column(sa.String(64), nullable=False)

    occurred_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    received_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utc_now)
    processed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
