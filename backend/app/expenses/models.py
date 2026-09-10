"""Expenses and how they reach a parcel's profit.

Master spec section 86. Its two rules shape the module:

* **allocation must be explicit** — the method and its version are stored next
  to every figure it produced, never inferred later;
* **do not rewrite historical profit silently when the seller changes a future
  allocation preference.** Changing the preference changes what happens next.
  Re-allocating an old period is a deliberate act that writes new snapshot
  revisions with a reason, so the old numbers stay readable.

An expense is not profit-affecting until it is allocated. Recording ৳5,000 of
ad spend does nothing to any parcel on its own; apportioning it does, and that
step names the method it used.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.common.money import BDT
from app.db.base import Base, PrimaryKeyMixin, TenantOwned, TimestampMixin
from app.db.types import GUID, JSONColumn, Paisa, TZDateTime

__all__ = [
    "ALLOCATION_VERSION",
    "AllocationMethod",
    "Expense",
    "ExpenseAllocation",
    "ExpenseKind",
]


#: Bumped when the allocation arithmetic changes. Stored on every allocation so
#: a figure produced under an older rule is identifiable rather than silently
#: mixed with newer ones (section 86).
ALLOCATION_VERSION = 1


class ExpenseKind(StrEnum):
    """What the money was spent on."""

    #: Facebook, Instagram, boosted posts. The one that actually varies with
    #: orders, and the only kind V1 allocates per parcel.
    AD_SPEND = "AD_SPEND"
    #: Rent, salaries, subscriptions. Section 18.1 keeps these out of
    #: contribution profit; they belong to the optional fixed-cost view.
    FIXED = "FIXED"
    #: Anything else the seller wants recorded.
    OTHER = "OTHER"

    @property
    def is_allocatable(self) -> bool:
        """Whether this kind may be pushed down onto parcels.

        Only ad spend. Section 18.1 is explicit that fixed costs sit *below*
        contribution profit, and section 86 warns against pretending fixed-cost
        allocation is accounting-grade — so this app does not do it per parcel.
        """
        return self is ExpenseKind.AD_SPEND


class AllocationMethod(StrEnum):
    """How an expense is apportioned (master spec section 86).

    The three V1 methods, verbatim. Each is a different answer to "which orders
    did this ad spend actually buy?", and none of them is right in general —
    which is why the seller chooses and the choice is recorded.
    """

    #: Every delivered parcel in the period carries the same share. Simple and
    #: defensible when order values are similar.
    EQUAL_PER_DELIVERED_ORDER = "EQUAL_PER_DELIVERED_ORDER"
    #: Share proportional to what each parcel actually collected. Puts more of
    #: the cost on the orders that brought more in.
    PROPORTIONAL_TO_REVENUE = "PROPORTIONAL_TO_REVENUE"
    #: For spend tagged to one product: shared across that product's delivered
    #: units only.
    PRODUCT_TAGGED_PER_UNIT = "PRODUCT_TAGGED_PER_UNIT"


class Expense(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """Money the seller spent, over a period."""

    __tablename__ = "expenses"
    __table_args__ = (
        sa.Index("ix_expenses_tenant_period", "tenant_id", "period_start"),
        sa.Index("ix_expenses_tenant_kind", "tenant_id", "kind"),
        sa.CheckConstraint("amount_paisa > 0", name="ck_expenses_amount_positive"),
        sa.CheckConstraint("period_end >= period_start", name="ck_expenses_period_ordered"),
    )

    kind: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default=BDT)
    amount_paisa: Mapped[int] = mapped_column(Paisa, nullable=False)

    #: The Asia/Dhaka days this spend covers. Inclusive at both ends, because a
    #: seller thinking "last week's ads" means both Sunday and Saturday.
    period_start: Mapped[date] = mapped_column(sa.Date, nullable=False)
    period_end: Mapped[date] = mapped_column(sa.Date, nullable=False)

    description: Mapped[str] = mapped_column(sa.String(200), nullable=False)

    #: Set when the spend was for one product, which is what makes
    #: ``PRODUCT_TAGGED_PER_UNIT`` possible.
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID,
        sa.ForeignKey("products.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    #: What the seller intends to use when this is allocated. A preference, not
    #: a record of what happened — that lives on the allocation rows.
    preferred_method: Mapped[str] = mapped_column(
        sa.String(40), nullable=False, default=AllocationMethod.EQUAL_PER_DELIVERED_ORDER
    )

    #: When it was last pushed down onto parcels. Null means it is recorded but
    #: is not yet affecting any profit figure.
    allocated_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    #: How much of it reached parcels. Less than ``amount_paisa`` when the
    #: period had no delivered parcels to carry it.
    allocated_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)

    metadata_json: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)

    @property
    def expense_kind(self) -> ExpenseKind:
        return ExpenseKind(self.kind)

    @property
    def allocation_preference(self) -> AllocationMethod:
        return AllocationMethod(self.preferred_method)

    @property
    def is_allocated(self) -> bool:
        return self.allocated_at is not None

    @property
    def unallocated_paisa(self) -> int:
        """Spend that reached no parcel.

        Shown to the seller rather than hidden: ad money spent in a week with
        no deliveries is a real cost that no order carries, and pretending
        otherwise would flatter every other order's margin.
        """
        return max(0, self.amount_paisa - self.allocated_paisa)


class ExpenseAllocation(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """One expense's share of one parcel.

    The audit trail behind a snapshot's ``ad_cost_paisa``: which expense, which
    method, which version. Without it, a seller asking "why does this order
    carry ৳120 of ad cost?" has no answer.
    """

    __tablename__ = "expense_allocations"
    __table_args__ = (
        # One share per expense per parcel per run. Re-allocating replaces the
        # run rather than adding a second share.
        sa.UniqueConstraint(
            "tenant_id",
            "expense_id",
            "consignment_id",
            name="uq_expense_allocations_expense_consignment",
        ),
        sa.Index("ix_expense_allocations_consignment", "consignment_id"),
        sa.CheckConstraint("amount_paisa >= 0", name="ck_expense_allocations_amount_non_negative"),
    )

    expense_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        sa.ForeignKey("expenses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    consignment_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        sa.ForeignKey("consignments.id", ondelete="CASCADE"),
        nullable=False,
    )

    amount_paisa: Mapped[int] = mapped_column(Paisa, nullable=False)

    method: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=ALLOCATION_VERSION)

    allocated_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)

    @property
    def allocation_method(self) -> AllocationMethod:
        return AllocationMethod(self.method)
