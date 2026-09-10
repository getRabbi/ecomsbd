"""COD receivables.

Master spec sections 15 and 10.3. A receivable is the answer to "how much does
the courier still owe me for this parcel?", and it is deliberately a *separate*
object from both the consignment and the payout.

Section 1.4 is the reason: **a parcel can be DELIVERED while its COD is still
unpaid.** Every system that collapses those two into one status ends up telling
a seller their money has arrived when it has not, which is precisely the loss
this product exists to prevent.
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
    "RECEIVABLE_TRANSITIONS",
    "CodReceivable",
    "ReceivableStatus",
    "can_transition",
]


class ReceivableStatus(StrEnum):
    """The frozen COD receivable lifecycle (master spec section 10.3).

    Values are stored verbatim.
    """

    #: The parcel has not been delivered, so nothing is owed yet.
    NOT_DUE = "NOT_DUE"
    #: Booked and on its way. Money is expected but not collectible.
    EXPECTED = "EXPECTED"
    #: Delivered. The courier is holding the seller's money.
    ELIGIBLE = "ELIGIBLE"
    #: A payout line has been identified as covering this, but is not applied.
    PAYOUT_IDENTIFIED = "PAYOUT_IDENTIFIED"
    #: Some of it has been paid.
    PARTIALLY_SETTLED = "PARTIALLY_SETTLED"
    #: Paid in full.
    SETTLED = "SETTLED"
    #: What arrived differs from what is owed by more than the tolerance.
    MISMATCHED = "MISMATCHED"
    #: The seller has raised it with the provider.
    DISPUTED = "DISPUTED"
    #: Given up as uncollectable, with a reason.
    WRITTEN_OFF = "WRITTEN_OFF"

    @property
    def is_open(self) -> bool:
        """Whether money is still outstanding on this receivable."""
        return self in (
            ReceivableStatus.EXPECTED,
            ReceivableStatus.ELIGIBLE,
            ReceivableStatus.PAYOUT_IDENTIFIED,
            ReceivableStatus.PARTIALLY_SETTLED,
            ReceivableStatus.MISMATCHED,
            ReceivableStatus.DISPUTED,
        )

    @property
    def is_collectible(self) -> bool:
        """Whether the courier is holding money for this right now.

        ``EXPECTED`` is not collectible: the parcel has not been delivered, so
        nobody owes anything yet. Counting it as outstanding would inflate a
        seller's expected cash by every parcel in transit.
        """
        return self in (
            ReceivableStatus.ELIGIBLE,
            ReceivableStatus.PAYOUT_IDENTIFIED,
            ReceivableStatus.PARTIALLY_SETTLED,
            ReceivableStatus.MISMATCHED,
            ReceivableStatus.DISPUTED,
        )


#: Legal transitions. A receivable can reach ``DISPUTED`` from any open state
#: because a seller may raise a case at any point, and it can always be written
#: off — but nothing moves backwards out of ``SETTLED`` except through an
#: explicit reversal, which goes back to the state the reversal implies.
RECEIVABLE_TRANSITIONS: dict[ReceivableStatus, frozenset[ReceivableStatus]] = {
    ReceivableStatus.NOT_DUE: frozenset(
        {
            ReceivableStatus.EXPECTED,
            ReceivableStatus.ELIGIBLE,
            ReceivableStatus.WRITTEN_OFF,
        }
    ),
    ReceivableStatus.EXPECTED: frozenset(
        {
            ReceivableStatus.ELIGIBLE,
            ReceivableStatus.NOT_DUE,
            ReceivableStatus.DISPUTED,
            ReceivableStatus.WRITTEN_OFF,
        }
    ),
    ReceivableStatus.ELIGIBLE: frozenset(
        {
            ReceivableStatus.PAYOUT_IDENTIFIED,
            ReceivableStatus.PARTIALLY_SETTLED,
            ReceivableStatus.SETTLED,
            ReceivableStatus.MISMATCHED,
            ReceivableStatus.DISPUTED,
            ReceivableStatus.WRITTEN_OFF,
            # A return recorded after a mistaken delivery takes it back out.
            ReceivableStatus.NOT_DUE,
        }
    ),
    ReceivableStatus.PAYOUT_IDENTIFIED: frozenset(
        {
            ReceivableStatus.PARTIALLY_SETTLED,
            ReceivableStatus.SETTLED,
            ReceivableStatus.MISMATCHED,
            ReceivableStatus.DISPUTED,
            ReceivableStatus.ELIGIBLE,
            ReceivableStatus.WRITTEN_OFF,
        }
    ),
    ReceivableStatus.PARTIALLY_SETTLED: frozenset(
        {
            ReceivableStatus.SETTLED,
            ReceivableStatus.MISMATCHED,
            ReceivableStatus.DISPUTED,
            ReceivableStatus.ELIGIBLE,
            ReceivableStatus.WRITTEN_OFF,
        }
    ),
    ReceivableStatus.SETTLED: frozenset(
        {
            # Only reachable by reversing a payout application.
            ReceivableStatus.PARTIALLY_SETTLED,
            ReceivableStatus.ELIGIBLE,
            ReceivableStatus.MISMATCHED,
            ReceivableStatus.DISPUTED,
        }
    ),
    ReceivableStatus.MISMATCHED: frozenset(
        {
            ReceivableStatus.PARTIALLY_SETTLED,
            ReceivableStatus.SETTLED,
            ReceivableStatus.DISPUTED,
            ReceivableStatus.ELIGIBLE,
            ReceivableStatus.WRITTEN_OFF,
        }
    ),
    ReceivableStatus.DISPUTED: frozenset(
        {
            ReceivableStatus.SETTLED,
            ReceivableStatus.PARTIALLY_SETTLED,
            ReceivableStatus.MISMATCHED,
            ReceivableStatus.ELIGIBLE,
            ReceivableStatus.WRITTEN_OFF,
        }
    ),
    #: Terminal. Recovering written-off money is a manual correction, which
    #: goes through the section 134 workflow and leaves an audit trail.
    ReceivableStatus.WRITTEN_OFF: frozenset(),
}


def can_transition(current: ReceivableStatus, target: ReceivableStatus) -> bool:
    if current is target:
        return True
    return target in RECEIVABLE_TRANSITIONS[current]


class CodReceivable(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """Money a courier owes a seller for one parcel."""

    __tablename__ = "cod_receivables"
    __table_args__ = (
        # One receivable per parcel. Two would double a shop's outstanding
        # balance the moment a status event was processed twice.
        sa.UniqueConstraint(
            "tenant_id", "consignment_id", name="uq_cod_receivables_tenant_consignment"
        ),
        sa.Index("ix_cod_receivables_tenant_status", "tenant_id", "status"),
        sa.Index("ix_cod_receivables_tenant_eligible", "tenant_id", "eligible_at"),
        sa.CheckConstraint(
            "collectible_paisa >= 0", name="ck_cod_receivables_collectible_non_negative"
        ),
        sa.CheckConstraint("settled_paisa >= 0", name="ck_cod_receivables_settled_non_negative"),
        # Section 17.2: settled total cannot exceed the collectible net without
        # an explicit adjustment — which is what `adjustment_paisa` records.
        sa.CheckConstraint(
            "settled_paisa <= collectible_paisa + adjustment_paisa",
            name="ck_cod_receivables_no_oversettlement",
        ),
    )

    consignment_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        sa.ForeignKey("consignments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        sa.ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: Denormalised so the Money screen can filter by courier without joining.
    provider: Mapped[str] = mapped_column(sa.String(40), nullable=False)

    status: Mapped[str] = mapped_column(
        sa.String(24), nullable=False, default=ReceivableStatus.NOT_DUE
    )

    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default=BDT)

    #: What the courier actually collected and therefore owes. For a partial
    #: delivery this is the delivered units only — section 17.8 forbids
    #: assuming the original COD.
    collectible_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)

    #: How much has been applied against it from payouts.
    settled_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)

    #: Explicit corrections. Kept separate from ``collectible_paisa`` so the
    #: original expectation stays legible next to what changed it.
    adjustment_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)

    #: Recognised provider deductions (delivery fee, COD fee, return fee).
    #: Visible in their own right rather than netted into the principal, so a
    #: seller can see what the courier took (section 84).
    deduction_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)

    #: When the parcel was delivered and the money became collectible. Drives
    #: COD aging.
    eligible_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    #: Asia/Dhaka day of ``eligible_at``, for day-grouped reporting.
    eligible_business_date: Mapped[date | None] = mapped_column(sa.Date, nullable=True)

    settled_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    #: Why it was written off or disputed.
    status_reason: Mapped[str | None] = mapped_column(sa.String(400), nullable=True)

    #: Bumped on every change, so an offline client can detect a conflict.
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)

    metadata_json: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)

    @property
    def receivable_status(self) -> ReceivableStatus:
        return ReceivableStatus(self.status)

    @property
    def expected_paisa(self) -> int:
        """What should arrive in total, after adjustments."""
        return self.collectible_paisa + self.adjustment_paisa

    @property
    def outstanding_paisa(self) -> int:
        """What the courier still owes.

        Master spec section 50: *"eligible collectible amount minus settled
        amount, adjusted for verified deductions."* Never negative — an
        overpayment is a reconciliation case, not a negative receivable, and
        letting it go below zero would quietly cancel out another parcel's
        genuine shortfall in the shop total.
        """
        if not self.receivable_status.is_collectible:
            return 0
        return max(0, self.expected_paisa - self.settled_paisa - self.deduction_paisa)

    @property
    def is_fully_settled(self) -> bool:
        return self.settled_paisa + self.deduction_paisa >= self.expected_paisa

    def age_in_days(self, *, as_of: datetime) -> int | None:
        """How long the money has been sitting with the courier."""
        if self.eligible_at is None:
            return None
        return max(0, (as_of - self.eligible_at).days)
