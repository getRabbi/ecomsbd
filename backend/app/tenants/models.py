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

__all__ = ["BusinessCategory", "OnboardingStep", "Tenant", "TenantStatus", "TenantUser"]


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
