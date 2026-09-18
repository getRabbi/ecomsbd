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
from datetime import date, datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utc_now
from app.db.base import Base, PrimaryKeyMixin, TenantOwned, TimestampMixin
from app.db.types import GUID, JSONColumn, Paisa, TZDateTime

__all__ = [
    "DISCREPANCY_STATUSES",
    "CaseEvent",
    "CaseEventAction",
    "CaseKind",
    "CasePriority",
    "CaseStatus",
    "ItemStatus",
    "ReconciliationCase",
    "ReconciliationItem",
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

    # --- V2.2 ---------------------------------------------------------------

    #: The COD arrived as expected but the courier charged a different amount
    #: than the charge on record (booking quote, seller figure or estimate).
    CHARGE_MISMATCH = "CHARGE_MISMATCH"
    #: A delivered parcel the courier left out: a statement row names it with
    #: charges and no COD, or it was delivered before everything a payout
    #: covers and is not in it.
    MISSING_COD = "MISSING_COD"
    #: A return charge that differs from the one on record.
    RETURN_CHARGE_MISMATCH = "RETURN_CHARGE_MISMATCH"


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
        CaseKind.CHARGE_MISMATCH,
        CaseKind.MISSING_COD,
        CaseKind.RETURN_CHARGE_MISMATCH,
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


class ItemStatus(StrEnum):
    """Where one parcel's money stands once expected is compared with actual.

    Derived, never authoritative: the ledger and the receivable hold the money
    truth. An item *explains* that truth for one parcel (or one statement row
    nobody could place), and is recomputed whenever the evidence moves.
    """

    #: What arrived agrees with what was owed, within tolerance.
    MATCHED = "MATCHED"
    #: Paid in more than one part, and the parts do not yet add up.
    PARTIAL = "PARTIAL"
    #: Money with no parcel behind it.
    UNMATCHED = "UNMATCHED"
    #: The same settlement row seen twice.
    DUPLICATE = "DUPLICATE"
    #: The COD the courier reports differs from what the parcel was owed.
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    #: The COD agrees but the courier's charge differs from the one on record.
    CHARGE_MISMATCH = "CHARGE_MISMATCH"
    #: A delivered parcel whose COD the courier did not pay.
    MISSING_COD = "MISSING_COD"
    #: A charge-only row for a parcel that came back.
    RETURN_ADJUSTMENT = "RETURN_ADJUSTMENT"
    #: The engine has a suggestion or a tie; a person decides.
    NEEDS_REVIEW = "NEEDS_REVIEW"

    @property
    def is_discrepancy(self) -> bool:
        return self in DISCREPANCY_STATUSES


#: Statuses that mean the money does not agree. Used by the summary and by
#: the "discrepancies only" filter, so both count the same things.
DISCREPANCY_STATUSES: frozenset[ItemStatus] = frozenset(
    {
        ItemStatus.PARTIAL,
        ItemStatus.AMOUNT_MISMATCH,
        ItemStatus.CHARGE_MISMATCH,
        ItemStatus.MISSING_COD,
    }
)


class ReconciliationItem(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """Expected against actual for one parcel, or one unplaced statement row.

    ``subject_key`` is ``receivable:<id>`` once a row is tied to a parcel — so a
    parcel paid in two parts is one item, not two half-mismatches — and
    ``line:<id>`` while it is not. Unique per tenant, so re-running
    reconciliation updates rather than accumulates.

    Every figure is copied from, or summed out of, records that already exist:
    the receivable, the payout lines and adjustments, and the charges on
    record. Nothing is estimated here, and nothing here is read back as money —
    the ledger stays the only financial truth.
    """

    __tablename__ = "reconciliation_items"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "subject_key", name="uq_reconciliation_items_subject"),
        # The review table filters by status and courier and pages by date.
        sa.Index("ix_reconciliation_items_tenant_status", "tenant_id", "status"),
        sa.Index(
            "ix_reconciliation_items_tenant_provider_date",
            "tenant_id",
            "provider",
            "settlement_date",
        ),
        sa.Index("ix_reconciliation_items_tenant_payout", "tenant_id", "payout_id"),
    )

    subject_key: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(24), nullable=False)
    provider: Mapped[str] = mapped_column(sa.String(40), nullable=False)

    payout_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    payout_line_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    receivable_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True, index=True)
    consignment_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    case_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)

    #: For search. The parcel's, or the row's when there is no parcel.
    merchant_reference: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)
    tracking_code: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)

    settlement_date: Mapped[date | None] = mapped_column(sa.Date, nullable=True)

    #: Null where there is nothing to expect — a row with no parcel.
    expected_cod_paisa: Mapped[int | None] = mapped_column(Paisa, nullable=True)
    actual_cod_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)

    #: Null when no charge is on record for the parcel. Not zero: "we do not
    #: know what it should cost" and "it should cost nothing" are different.
    expected_charge_paisa: Mapped[int | None] = mapped_column(Paisa, nullable=True)
    #: The ``ChargeSource`` the expected charge came from; null when missing.
    expected_charge_source: Mapped[str | None] = mapped_column(sa.String(16), nullable=True)
    actual_charge_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)

    expected_net_paisa: Mapped[int | None] = mapped_column(Paisa, nullable=True)
    actual_net_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)
    #: ``actual_net - expected_net``. Negative means the seller got less.
    difference_paisa: Mapped[int | None] = mapped_column(Paisa, nullable=True)

    #: Deductions on the statement not yet accepted into the ledger.
    charges_pending_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)
    line_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    #: Breakdown for the detail view: charges by type, lines, reasons.
    detail: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)

    evaluated_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)

    @property
    def item_status(self) -> ItemStatus:
        return ItemStatus(self.status)


class CaseEventAction(StrEnum):
    OPENED = "OPENED"
    NOTE = "NOTE"
    STATUS_CHANGED = "STATUS_CHANGED"
    REOPENED = "REOPENED"
    #: The engine closed it because the money it was about has since arrived
    #: or been explained.
    AUTO_RESOLVED = "AUTO_RESOLVED"
    MANUAL_MATCH = "MANUAL_MATCH"
    CHARGES_ACCEPTED = "CHARGES_ACCEPTED"


class CaseEvent(Base, TenantOwned, PrimaryKeyMixin):
    """One thing that happened to a case. Append-only.

    The case row carries the current state; this carries how it got there —
    who looked, what they said, when it was reopened — so a decision about
    money can be explained months later.
    """

    __tablename__ = "reconciliation_case_events"
    __table_args__ = (sa.Index("ix_reconciliation_case_events_case", "case_id", "created_at"),)

    case_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        sa.ForeignKey("reconciliation_cases.id", ondelete="CASCADE"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(sa.String(24), nullable=False)
    from_status: Mapped[str | None] = mapped_column(sa.String(16), nullable=True)
    to_status: Mapped[str | None] = mapped_column(sa.String(16), nullable=True)
    note: Mapped[str | None] = mapped_column(sa.String(1000), nullable=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utc_now)
