"""Reconciliation cases: the things a seller has to look at.

Master spec section 16 lists these by name and requires *a separate issue
entity* for each. That separation is the point — a case is a piece of work with
an owner and an outcome, not a colour on a row. "Delivered but unpaid" that
exists only as a filter on a list is a problem nobody is accountable for.

Cases are opened by the engine and resolved by a person. Nothing here changes a
balance: resolving a case records a decision, and any money that moves as a
result goes through the receivable service so it lands in the ledger.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PrimaryKeyMixin, TenantOwned, TimestampMixin
from app.db.types import GUID, JSONColumn, Paisa, TZDateTime

__all__ = [
    "CaseKind",
    "CasePriority",
    "CaseStatus",
    "ReconciliationCase",
]


class CaseKind(StrEnum):
    """The eight cases master spec section 16 requires."""

    #: A parcel was delivered and the money has not arrived within the
    #: configured window. The single most valuable thing this product finds.
    DELIVERED_BUT_UNPAID = "DELIVERED_BUT_UNPAID"
    #: Less arrived than was owed, beyond tolerance.
    UNDERPAID = "UNDERPAID"
    #: More arrived than was owed. Not a windfall — usually somebody else's
    #: money, and settling it silently makes it the seller's problem later.
    OVERPAID = "OVERPAID"
    #: The provider took something off and we could not tell what it was.
    UNKNOWN_DEDUCTION = "UNKNOWN_DEDUCTION"
    #: The same statement line appears twice.
    DUPLICATE_PAYOUT_LINE = "DUPLICATE_PAYOUT_LINE"
    #: A payout line names nothing this shop has.
    UNMAPPABLE_PAYOUT = "UNMAPPABLE_PAYOUT"
    #: A parcel has been in transit far longer than it should be.
    STALE_IN_TRANSIT = "STALE_IN_TRANSIT"
    #: A parcel came back but its units were never put back on the shelf.
    RETURNED_NOT_RESTOCKED = "RETURNED_NOT_RESTOCKED"


class CaseStatus(StrEnum):
    OPEN = "OPEN"
    #: The seller has raised it with the provider.
    IN_PROGRESS = "IN_PROGRESS"
    #: Sorted out. The resolution says how.
    RESOLVED = "RESOLVED"
    #: Looked at and judged not to be a problem. Distinct from RESOLVED so the
    #: engine can stop re-opening it without pretending it was fixed.
    DISMISSED = "DISMISSED"

    @property
    def is_open(self) -> bool:
        return self in (CaseStatus.OPEN, CaseStatus.IN_PROGRESS)


class CasePriority(StrEnum):
    """How loudly to surface it.

    Derived from the money at stake and the kind, not set by hand: a seller
    with forty open cases needs the list ordered by what it costs them.
    """

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ReconciliationCase(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """One thing that needs a human."""

    __tablename__ = "reconciliation_cases"
    __table_args__ = (
        # One open case per (kind, subject). Re-running the engine must not
        # produce a second copy of a case the seller is already working on —
        # a list that grows every scan is a list nobody reads.
        sa.UniqueConstraint(
            "tenant_id",
            "kind",
            "subject_type",
            "subject_id",
            "dedupe_key",
            name="uq_reconciliation_cases_subject",
        ),
        sa.Index("ix_reconciliation_cases_tenant_status", "tenant_id", "status"),
        sa.Index("ix_reconciliation_cases_tenant_kind", "tenant_id", "kind"),
        sa.CheckConstraint("amount_paisa >= 0", name="ck_reconciliation_cases_amount_non_negative"),
    )

    kind: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default=CaseStatus.OPEN)
    priority: Mapped[str] = mapped_column(sa.String(8), nullable=False, default=CasePriority.MEDIUM)

    #: What the case is about: ``cod_receivable``, ``payout_line``,
    #: ``consignment``.
    subject_type: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)

    #: Distinguishes two cases of the same kind about the same subject that are
    #: genuinely different — an underpayment reopened at a new amount, say.
    #: Empty string rather than null so the unique constraint works on every
    #: database (a null never equals a null).
    dedupe_key: Mapped[str] = mapped_column(sa.String(120), nullable=False, default="")

    #: The money at stake. Zero for cases that are not about an amount.
    amount_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)

    #: Seller-facing, in plain words. The engine writes this; the UI does not
    #: reword it, so support and the seller are reading the same sentence.
    summary: Mapped[str] = mapped_column(sa.String(400), nullable=False)

    #: Everything needed to explain the case without re-deriving it.
    detail: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)

    #: Related records, so the UI can link straight to them.
    receivable_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    payout_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    payout_line_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    consignment_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)

    opened_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    resolution: Mapped[str | None] = mapped_column(sa.String(400), nullable=True)

    @property
    def case_kind(self) -> CaseKind:
        return CaseKind(self.kind)

    @property
    def case_status(self) -> CaseStatus:
        return CaseStatus(self.status)

    @property
    def case_priority(self) -> CasePriority:
        return CasePriority(self.priority)


#: Cases whose priority is driven by the amount at stake rather than by kind.
#: An unmappable ৳50 line is not urgent; an unpaid ৳14,050 parcel is.
_AMOUNT_DRIVEN: frozenset[CaseKind] = frozenset(
    {
        CaseKind.DELIVERED_BUT_UNPAID,
        CaseKind.UNDERPAID,
        CaseKind.OVERPAID,
        CaseKind.UNKNOWN_DEDUCTION,
        CaseKind.UNMAPPABLE_PAYOUT,
    }
)

#: Above this, a money case is HIGH. ৳5,000 is roughly a day's takings for the
#: sellers this targets, which is the point at which it stops being noise.
HIGH_PRIORITY_THRESHOLD_PAISA = 500_000


def priority_for(kind: CaseKind, amount_paisa: int) -> CasePriority:
    """How urgent a case is.

    Duplicate lines and stale parcels are always worth a look regardless of
    amount: a duplicate can double a settlement, and a stale parcel is usually
    lost rather than slow.
    """
    if kind in (CaseKind.DUPLICATE_PAYOUT_LINE, CaseKind.STALE_IN_TRANSIT):
        return CasePriority.HIGH
    if kind is CaseKind.RETURNED_NOT_RESTOCKED:
        return CasePriority.MEDIUM
    if kind in _AMOUNT_DRIVEN:
        if amount_paisa >= HIGH_PRIORITY_THRESHOLD_PAISA:
            return CasePriority.HIGH
        return CasePriority.MEDIUM if amount_paisa > 0 else CasePriority.LOW
    return CasePriority.MEDIUM
