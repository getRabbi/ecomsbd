"""Shop referrals (V4 WP7.4).

``referral_codes``: one shareable code per shop. ``referrals``: one row per
referred shop, owned by the **referrer** (``tenant_id`` is the code's shop), so
the referrer's own list is an ordinary tenant-scoped read. The referred shop
appears only as ``referred_tenant_id``; its name never leaves its own tenant.

When the referred shop's first payment is approved, both shops get a 30-day
plan credit through the admin-manual grant (``BillingService.
grant_manual_subscription``), once: the row moves PENDING → REWARDED under a
lock, and each grant carries the unique business key
``referral:<id>:referred`` / ``referral:<id>:referrer`` as its billing
``provider_event_id``. Ids, codes and states only: no phone, name or message.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PrimaryKeyMixin, TenantOwned, TimestampMixin
from app.db.types import GUID, TZDateTime

__all__ = [
    "Referral",
    "ReferralCode",
    "ReferralCreditState",
    "ReferralHoldReason",
    "ReferralState",
    "ReferralVoidReason",
]


class ReferralState(StrEnum):
    #: Code applied; waiting for the referred shop's first approved payment.
    PENDING = "PENDING"
    #: The first payment was approved and the credits were handled.
    REWARDED = "REWARDED"
    #: Can never be rewarded (``void_reason`` says why).
    VOID = "VOID"


class ReferralVoidReason(StrEnum):
    #: The approved payment was not the shop's first (a store purchase or an
    #: earlier TrxID payment came first, e.g. while the feature was off).
    NOT_FIRST_PAYMENT = "NOT_FIRST_PAYMENT"


class ReferralCreditState(StrEnum):
    PENDING = "PENDING"
    #: The 30 days were added through the admin-manual grant.
    GRANTED = "GRANTED"
    #: Not added automatically (``referrer_hold_reason``); support decides.
    HELD = "HELD"


class ReferralHoldReason(StrEnum):
    #: The referrer pays through a billing provider (Play, bKash web): a manual
    #: grant would replace the provider's row and be undone on its next sync.
    PROVIDER_SUBSCRIPTION = "PROVIDER_SUBSCRIPTION"
    #: The referrer's shop is suspended or being deleted.
    SHOP_NOT_ACTIVE = "SHOP_NOT_ACTIVE"


class ReferralCode(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "referral_codes"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", name="uq_referral_codes_tenant"),
        sa.UniqueConstraint("code", name="uq_referral_codes_code"),
    )

    code: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)


class Referral(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "referrals"
    __table_args__ = (
        # A shop is referred once, ever.
        sa.UniqueConstraint("referred_tenant_id", name="uq_referrals_referred_tenant"),
        # One payment rewards at most one referral.
        sa.UniqueConstraint("qualifying_submission_id", name="uq_referrals_submission"),
        sa.CheckConstraint("referred_tenant_id <> tenant_id", name="ck_referrals_not_self"),
        sa.CheckConstraint(
            "(state = 'PENDING' AND rewarded_at IS NULL)"
            " OR (state = 'REWARDED' AND rewarded_at IS NOT NULL"
            " AND qualifying_submission_id IS NOT NULL)"
            " OR (state = 'VOID' AND void_reason IS NOT NULL)",
            name="ck_referrals_state",
        ),
        sa.CheckConstraint(
            "(referrer_credit_state = 'HELD') = (referrer_hold_reason IS NOT NULL)",
            name="ck_referrals_hold_reason",
        ),
    )

    referral_code_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("referral_codes.id", ondelete="RESTRICT"), nullable=False
    )
    referred_tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    applied_by_user_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    state: Mapped[str] = mapped_column(sa.String(12), nullable=False, default=ReferralState.PENDING)
    void_reason: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)
    qualifying_submission_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID,
        sa.ForeignKey("billing_payment_submissions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    rewarded_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    referred_credit_state: Mapped[str] = mapped_column(
        sa.String(12), nullable=False, default=ReferralCreditState.PENDING
    )
    referrer_credit_state: Mapped[str] = mapped_column(
        sa.String(12), nullable=False, default=ReferralCreditState.PENDING
    )
    referrer_hold_reason: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)
