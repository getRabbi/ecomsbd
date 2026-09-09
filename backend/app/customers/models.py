"""Customer CRM and addresses.

Master spec sections 21, 31, 71 and 133.

Two rules define this module.

**The customer belongs to one seller.** Section 21: "no cross-seller customer
browsing." Every derived metric here — order count, success rate, lifetime
value — is computed from *this tenant's own* orders. There is deliberately no
shared reputation, no global counter and no cross-seller blacklist (section 130
forbids it outright).

**The phone number is the sensitive part.** Section 133: an encrypted value for
display, a keyed HMAC for exact lookup, and the last four digits for support.
The canonical number is never stored in clear text and never returned by the
API. A plain hash would not help — the Bangladeshi mobile keyspace is about a
billion numbers, enumerable offline in minutes.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.clock import utc_now
from app.db.base import Base, PrimaryKeyMixin, SoftDeleteMixin, TenantOwned, TimestampMixin
from app.db.types import GUID, JSONColumn, Paisa, TZDateTime

__all__ = ["Customer", "CustomerAddress", "CustomerFlag"]


class CustomerFlag(StrEnum):
    """Seller-private markers.

    Private by construction: these never leave the tenant, are never
    aggregated across sellers, and never appear in another seller's app.
    Section 130 permits "seller-private block notes" and forbids anything that
    would make them a shared judgement about a person.
    """

    NONE = "NONE"
    STARRED = "STARRED"
    BLOCKED = "BLOCKED"


class Customer(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """One buyer, as known to one seller."""

    __tablename__ = "customers"
    __table_args__ = (
        # One record per phone per shop. The same person buying from two shops
        # is two independent rows that never see each other.
        sa.UniqueConstraint(
            "tenant_id", "phone_search_hmac", name="uq_customers_tenant_id_phone_search_hmac"
        ),
        sa.Index("ix_customers_tenant_phone", "tenant_id", "phone_search_hmac"),
        sa.Index("ix_customers_tenant_created_at", "tenant_id", "created_at"),
        sa.Index("ix_customers_tenant_last_order", "tenant_id", "last_order_at"),
        sa.CheckConstraint("order_count >= 0", name="order_count_non_negative"),
        sa.CheckConstraint("delivered_count >= 0", name="delivered_count_non_negative"),
        sa.CheckConstraint("returned_count >= 0", name="returned_count_non_negative"),
    )

    name: Mapped[str | None] = mapped_column(sa.String(160), nullable=True)

    # --- phone (section 133) -------------------------------------------------
    #: Keyed HMAC of the canonical +8801XXXXXXXXX form. The only lookup key.
    phone_search_hmac: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    #: AES-GCM envelope. Decrypted only on an authorised reveal path.
    phone_enc: Mapped[str] = mapped_column(sa.Text, nullable=False)
    phone_last4: Mapped[str] = mapped_column(sa.String(4), nullable=False)
    #: Pre-computed `01712****78` for lists, so rendering a page of customers
    #: does not require decrypting every row.
    phone_masked: Mapped[str] = mapped_column(sa.String(20), nullable=False)

    #: Optional second number, same treatment.
    alt_phone_enc: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    alt_phone_masked: Mapped[str | None] = mapped_column(sa.String(20), nullable=True)

    # --- seller-private ------------------------------------------------------
    notes: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    flag: Mapped[str] = mapped_column(sa.String(16), nullable=False, default=CustomerFlag.NONE)
    flag_reason: Mapped[str | None] = mapped_column(sa.String(400), nullable=True)

    # --- derived from this tenant's own orders only --------------------------
    # Denormalised so a customer list does not aggregate orders per row.
    # `CustomerService.recalculate_metrics` rebuilds them from the orders table.
    order_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    delivered_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    returned_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    cancelled_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    #: Revenue actually collected, not merely ordered (master spec section 50).
    #: Stays zero until the money engine lands in Phase D.
    realized_revenue_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)

    first_order_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    last_order_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    metadata_json: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)

    addresses: Mapped[list[CustomerAddress]] = relationship(
        back_populates="customer",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )

    # --- derived properties --------------------------------------------------

    @property
    def is_blocked(self) -> bool:
        return self.flag == CustomerFlag.BLOCKED

    @property
    def is_starred(self) -> bool:
        return self.flag == CustomerFlag.STARRED

    @property
    def is_repeat_buyer(self) -> bool:
        """Two or more orders with this seller."""
        return self.order_count >= 2

    @property
    def terminal_count(self) -> int:
        """Orders that reached a final outcome, the denominator for success."""
        return self.delivered_count + self.returned_count + self.cancelled_count

    @property
    def success_rate_basis_points(self) -> int | None:
        """Own-business delivery success, in basis points.

        ``None`` rather than 0% when there is no history: showing a brand-new
        customer as "0% success" would read as a judgement the data does not
        support (master spec sections 24, 121 on sample size).
        """
        if self.terminal_count == 0:
            return None
        return round(self.delivered_count * 10_000 / self.terminal_count)

    def default_address(self) -> CustomerAddress | None:
        for address in self.addresses:
            if address.is_default:
                return address
        return self.addresses[0] if self.addresses else None


class CustomerAddress(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """A delivery address.

    Master spec section 71: the seller's raw text is evidence and is never
    overwritten. A courier's normalised version of the same address is stored
    separately, because a provider rewriting "Mirpur 10" into its own area
    string must not destroy what the customer actually said.
    """

    __tablename__ = "customer_addresses"
    __table_args__ = (sa.Index("ix_customer_addresses_customer", "customer_id", "is_default"),)

    customer_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        sa.ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: Exactly what the seller or customer typed. Never rewritten.
    raw_address: Mapped[str] = mapped_column(sa.Text, nullable=False)
    #: Our own tidy-up: whitespace collapsed, numerals normalised. Still ours,
    #: not a provider's.
    normalized_address: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    label: Mapped[str | None] = mapped_column(sa.String(60), nullable=True)
    division: Mapped[str | None] = mapped_column(sa.String(80), nullable=True)
    district: Mapped[str | None] = mapped_column(sa.String(80), nullable=True)
    city: Mapped[str | None] = mapped_column(sa.String(80), nullable=True)
    area: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(sa.String(16), nullable=True)

    #: Provider-side location identifiers, keyed by provider. Written by the
    #: courier adapters in Phase C; a separate snapshot, never the canonical
    #: address (section 71).
    provider_location_refs: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)

    is_default: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    last_used_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    customer: Mapped[Customer] = relationship(back_populates="addresses")

    def touch(self) -> None:
        self.last_used_at = utc_now()
