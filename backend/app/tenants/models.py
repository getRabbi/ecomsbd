"""Tenant and membership models.

A *tenant* is one seller's shop. Master spec section 31 requires every
business-owned table to carry ``tenant_id``; :class:`Tenant` is the row that
identifier points at, so it is not itself :class:`~app.db.base.TenantOwned`.

:class:`TenantUser` **is** tenant-owned, which means listing a shop's members is
automatically scoped. The one query that must cross tenants — "which shops does
this phone number belong to?", asked during login before a tenant is known —
goes through an explicit, logged bypass in the auth service.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.clock import utc_now
from app.db.base import Base, PrimaryKeyMixin, SoftDeleteMixin, TenantOwned, TimestampMixin
from app.db.types import GUID, JSONColumn, TZDateTime
from app.tenants.roles import TenantRole
from app.users.models import User

__all__ = [
    "BusinessCategory",
    "InvitationStatus",
    "OnboardingStep",
    "Tenant",
    "TenantInvitation",
    "TenantStatus",
    "TenantUser",
]


class TenantStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    #: Deletion requested; retained per the policy in master spec section 100.
    PENDING_DELETION = "PENDING_DELETION"


class OnboardingStep(StrEnum):
    """Onboarding progress (master spec section 4).

    Persisted server-side rather than in app storage so a seller who reinstalls
    resumes where they left off instead of starting over.
    """

    SHOP_DETAILS = "SHOP_DETAILS"
    PICKUP_ADDRESS = "PICKUP_ADDRESS"
    COURIER_CHOICE = "COURIER_CHOICE"
    FIRST_PRODUCT = "FIRST_PRODUCT"
    COMPLETE = "COMPLETE"


class BusinessCategory(StrEnum):
    """Seller's category.

    Collected to support future privacy-safe benchmarks. Master spec section 120
    forbids showing any benchmark until a sufficient anonymised dataset exists,
    so today this field is descriptive only.
    """

    CLOTHING = "CLOTHING"
    ELECTRONICS = "ELECTRONICS"
    COSMETICS = "COSMETICS"
    FOOD = "FOOD"
    HOME = "HOME"
    JEWELLERY = "JEWELLERY"
    BOOKS = "BOOKS"
    BABY = "BABY"
    OTHER = "OTHER"


class Tenant(Base, PrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """One seller's shop."""

    __tablename__ = "tenants"
    __table_args__ = (sa.Index("ix_tenants_status_created_at", "status", "created_at"),)

    name: Mapped[str] = mapped_column(sa.String(160), nullable=False)
    business_category: Mapped[str] = mapped_column(
        sa.String(40), nullable=False, default=BusinessCategory.OTHER
    )
    status: Mapped[str] = mapped_column(
        sa.String(24), nullable=False, default=TenantStatus.ACTIVE, index=True
    )

    #: Stored even though V1 defaults every tenant to Asia/Dhaka (section 69).
    timezone: Mapped[str] = mapped_column(sa.String(64), nullable=False, default="Asia/Dhaka")
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default="BDT")

    #: Seller-facing order numbers: ``CP-20260909-0042`` (master spec section 70).
    #: Configurable per tenant; the default keeps existing references searchable.
    order_number_prefix: Mapped[str] = mapped_column(sa.String(8), nullable=False, default="CP")

    # --- pickup address (section 71: raw text is preserved as evidence) ------
    pickup_contact_name: Mapped[str | None] = mapped_column(sa.String(160), nullable=True)
    #: Encrypted at rest; the searchable form lives on the owning user.
    pickup_phone_enc: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    pickup_phone_last4: Mapped[str | None] = mapped_column(sa.String(4), nullable=True)
    pickup_address_raw: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    pickup_address_normalized: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    pickup_district: Mapped[str | None] = mapped_column(sa.String(80), nullable=True)
    pickup_area: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)

    onboarding_step: Mapped[str] = mapped_column(
        sa.String(32), nullable=False, default=OnboardingStep.SHOP_DETAILS
    )
    onboarding_completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    #: Non-authoritative preferences (alert thresholds, display choices).
    settings: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)

    members: Mapped[list[TenantUser]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def onboarding_complete(self) -> bool:
        return self.onboarding_step == OnboardingStep.COMPLETE


class TenantUser(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """Membership of a user in a shop, with a role."""

    __tablename__ = "tenant_users"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_tenant_users_tenant_id_user_id"),
        sa.Index("ix_tenant_users_user_id_tenant_id", "user_id", "tenant_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(sa.String(24), nullable=False, default=TenantRole.OWNER)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    invited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    joined_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utc_now)

    tenant: Mapped[Tenant] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships")


class InvitationStatus(StrEnum):
    """Where an invitation stands.

    Terminal states are kept rather than deleted: "who invited this person, and
    when did they accept?" is a question an owner asks months later, and a row
    that removes itself cannot answer it.
    """

    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    #: Withdrawn by the shop before it was accepted.
    REVOKED = "REVOKED"
    #: Ran out of time. Expiry is evaluated on read, not by a sweeper job.
    EXPIRED = "EXPIRED"

    @property
    def is_open(self) -> bool:
        return self is InvitationStatus.PENDING


class TenantInvitation(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """An offer of membership, before the person has accepted it.

    Keyed on the **phone number**, not on a mailed token. Sign-in is OTP on a
    Bangladeshi mobile, so the number already is the identity: a token would add
    a second secret to leak without proving anything the OTP does not already
    prove. Acceptance therefore requires an authenticated session whose own
    phone matches the invitation — possession of the number, verified the same
    way every login is.

    The number is stored the way :class:`~app.users.models.User` stores it: an
    HMAC for lookup, ciphertext for display, last four for recognition. No
    plaintext, and the search hash is what an invitation is found by.
    """

    __tablename__ = "tenant_invitations"
    __table_args__ = (
        # One open invitation per number per shop. A partial unique index would
        # be tighter, but it is not portable to SQLite, which the test suite
        # migrates; the service enforces the same rule on the way in and this
        # index is what makes the lookup fast.
        sa.Index(
            "ix_tenant_invitations_tenant_phone",
            "tenant_id",
            "phone_search_hmac",
        ),
        # The invitee's own lookup: "which shops are waiting for me?", asked
        # before any tenant is known.
        sa.Index("ix_tenant_invitations_phone_status", "phone_search_hmac", "status"),
    )

    phone_search_hmac: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    phone_enc: Mapped[str] = mapped_column(sa.Text, nullable=False)
    phone_last4: Mapped[str] = mapped_column(sa.String(4), nullable=False)

    role: Mapped[str] = mapped_column(
        sa.String(24), nullable=False, default=TenantRole.ORDER_OPERATOR
    )
    status: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, default=InvitationStatus.PENDING
    )

    display_name: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)
    invited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)

    expires_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    accepted_user_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    @property
    def invitation_status(self) -> InvitationStatus:
        try:
            return InvitationStatus(self.status)
        except ValueError:
            return InvitationStatus.EXPIRED

    def is_expired(self, *, now: datetime | None = None) -> bool:
        return (now or utc_now()) >= self.expires_at

    @property
    def is_claimable(self) -> bool:
        """Whether this invitation can still be accepted right now."""
        return self.invitation_status.is_open and not self.is_expired()

    def effective_status(self) -> InvitationStatus:
        """What this invitation *is*, accounting for the clock.

        Expiry is derived rather than swept: a job that has not run yet must
        not leave an out-of-date invitation looking acceptable.
        """
        if self.invitation_status.is_open and self.is_expired():
            return InvitationStatus.EXPIRED
        return self.invitation_status
