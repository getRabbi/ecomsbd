"""Subscription state.

Master spec sections 27.3 and 93: entitlement derives from a **verified**
subscription state, never the other way round, and the mobile app never trusts a
local ``paid=true``.

Phase A ships the state model and the resolution path. Actually verifying a
purchase — Google Play Developer API, bKash merchant callbacks — belongs to
Phase F and needs credentials this project does not have, so nothing here can
grant a paid plan on its own. ``source`` records how a subscription came to
exist, and today only ``MANUAL_ADMIN`` can write one.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

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


class SubscriptionSource(StrEnum):
    """Where a subscription came from (master spec section 27.3)."""

    PLAY = "play"
    BKASH_WEB = "bkash_web"
    #: Support credit or plan extension. The only source writable in Phase A,
    #: and every write is audited.
    MANUAL_ADMIN = "manual_admin"


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

    current_period_start: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    current_period_end: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    #: Provider-side identifiers, written only after a verified provider response.
    provider_reference: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    provider_customer_reference: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    #: When the provider state was last confirmed by us, not claimed by a client.
    verified_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    metadata_json: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)
    granted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    grant_reason: Mapped[str | None] = mapped_column(sa.String(400), nullable=True)

    def is_current(self, *, at: datetime) -> bool:
        """Whether this subscription grants access at a given instant."""
        if not SubscriptionStatus(self.status).grants_access:
            return False
        return not (self.current_period_end is not None and self.current_period_end <= at)
