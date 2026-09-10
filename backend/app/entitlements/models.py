"""Subscription state.

Master spec sections 27.3 and 93: entitlement derives from a **verified**
subscription state, never the other way round, and the mobile app never trusts a
local ``paid=true``.

Phase A shipped the state model and the resolution path. Phase F completes it:
the provider interface, the verification flow, dunning, grace and the
reconciliation job all now write here, and every transition is recorded in
``subscription_events``.

What has *not* changed is the rule: ``source`` records which provider owns the
subscription, and a paid plan is granted only after a provider response we
verified ourselves. Play and bKash both need external configuration that does
not exist yet (``PLAY_BILLING_EXTERNAL_CONFIGURATION_REQUIRED``,
``BKASH_MERCHANT_SETUP_REQUIRED``), so in this build only ``MANUAL_ADMIN`` can
actually complete a grant.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.billing.models import BillingProviderKind, DistributionChannel
from app.db.base import Base, PrimaryKeyMixin, TenantOwned, TimestampMixin
from app.db.types import JSONColumn, TZDateTime
from app.entitlements.catalog import PlanCode

__all__ = ["Subscription", "SubscriptionSource", "SubscriptionStatus"]


class SubscriptionStatus(StrEnum):
    """Master spec section 93."""

    TRIAL = "TRIAL"
    ACTIVE = "ACTIVE"
    #: Payment failed but access continues briefly while dunning runs.
    GRACE = "GRACE"
    PAST_DUE = "PAST_DUE"
    CANCEL_AT_PERIOD_END = "CANCEL_AT_PERIOD_END"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    REFUNDED = "REFUNDED"
    SUSPENDED = "SUSPENDED"

    @property
    def grants_access(self) -> bool:
        """Whether this state entitles the tenant to its paid plan."""
        return self in (
            SubscriptionStatus.TRIAL,
            SubscriptionStatus.ACTIVE,
            SubscriptionStatus.GRACE,
            SubscriptionStatus.CANCEL_AT_PERIOD_END,
        )


#: Where a subscription came from (master spec section 27.3).
#:
#: This is an alias, not a second enum. Phase A named it ``SubscriptionSource``
#: and Phase F needs the same three values as *the billing provider* in
#: :mod:`app.billing.models`. Two enums holding identical strings is two places
#: to add a provider and one place to forget.
SubscriptionSource = BillingProviderKind


class Subscription(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """One shop's subscription record."""

    __tablename__ = "subscriptions"
    __table_args__ = (
        sa.Index("ix_subscriptions_tenant_status", "tenant_id", "status"),
        sa.Index("ix_subscriptions_current_period_end", "current_period_end"),
    )

    plan_code: Mapped[str] = mapped_column(sa.String(32), nullable=False, default=PlanCode.FREE)
    status: Mapped[str] = mapped_column(
        sa.String(32), nullable=False, default=SubscriptionStatus.ACTIVE
    )
    source: Mapped[str] = mapped_column(
        sa.String(32), nullable=False, default=SubscriptionSource.MANUAL_ADMIN
    )

    trial_end: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    current_period_start: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    current_period_end: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    #: Set when the seller cancels but has already paid for the rest of the
    #: period. Access continues to ``current_period_end`` — taking it away at
    #: the moment of cancellation would be charging for time not given.
    #: ``server_default`` matches the migration, so the two cannot drift and
    #: autogenerate stays quiet about a column that is deliberately defaulted.
    cancel_at_period_end: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false()
    )

    #: Dunning window (master spec section 91). Access continues while a failed
    #: payment is retried; ``NULL`` outside a dunning cycle.
    grace_until: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    #: Provider-side identifiers, written only after a verified provider response.
    provider_reference: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    provider_customer_reference: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    #: The provider's own product/SKU id. Kept separate from ``plan_code``: the
    #: mapping between them is configuration and can change per channel.
    provider_product_id: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)

    #: Which build sold this. Decides whether an external payment CTA is legal
    #: to show (master spec section 27.1).
    distribution_channel: Mapped[str] = mapped_column(
        sa.String(20),
        nullable=False,
        default=DistributionChannel.DIRECT,
        server_default=str(DistributionChannel.DIRECT),
    )

    #: When the provider state was last confirmed by us, not claimed by a client.
    verified_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    #: Last time a *successful* provider sync completed, whether or not it
    #: changed anything. A stale value is how the reconciliation job finds
    #: subscriptions the provider has stopped telling us about.
    last_synced_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    #: Why the subscription holds its current status, in support's words.
    #: The machine-readable history is ``subscription_events``.
    status_reason: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)

    metadata_json: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)
    granted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    grant_reason: Mapped[str | None] = mapped_column(sa.String(400), nullable=True)

    def is_current(self, *, at: datetime) -> bool:
        """Whether this subscription grants access at a given instant.

        Two windows can extend access past ``current_period_end``:

        *   ``TRIAL`` runs to ``trial_end``;
        *   a dunning cycle runs to ``grace_until``.

        Both are checked here rather than at each call site, so no caller can
        forget one and silently cut a paying seller off mid-retry.
        """
        status = SubscriptionStatus(self.status)
        if not status.grants_access:
            return False

        if status is SubscriptionStatus.TRIAL and self.trial_end is not None:
            return self.trial_end > at

        if self.current_period_end is not None and self.current_period_end <= at:
            # Expired period, but a grace window may still be open.
            return self.grace_until is not None and self.grace_until > at

        return True

    def access_until(self) -> datetime | None:
        """The latest instant this subscription still grants access, if bounded."""
        candidates = [
            value
            for value in (self.trial_end, self.current_period_end, self.grace_until)
            if value is not None
        ]
        return max(candidates) if candidates else None
