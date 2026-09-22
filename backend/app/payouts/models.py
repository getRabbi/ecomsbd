"""Payouts: the money a courier says it has sent.

Master spec sections 16, 81, 83 and 84. A payout is what arrived — a statement,
an API response, or a figure the seller typed in. It is deliberately kept as
*evidence* rather than as a set of balance updates: section 81.4 forbids
reconciliation from ever deleting source statement data, and section 83 exists
so support can explain a reconciliation result months later.

That is why every line keeps the raw row it was parsed from, why the file's
SHA-256 is stored, and why an unmatched line is a normal state rather than an
error (section 81.3).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.money import BDT
from app.db.base import Base, PrimaryKeyMixin, TenantOwned, TimestampMixin
from app.db.types import GUID, JSONColumn, Paisa, TZDateTime

__all__ = [
    "AdjustmentType",
    "MatchConfidence",
    "Payout",
    "PayoutAdjustment",
    "PayoutLine",
    "PayoutLineStatus",
    "PayoutSource",
    "PayoutSourceFile",
    "PayoutStatus",
]


class PayoutSource(StrEnum):
    """How the payout reached us (master spec section 16)."""

    #: A provider payment API. No provider is integrated yet; this exists so
    #: the model does not need changing when one is.
    API = "API"
    #: A CSV or statement the seller uploaded.
    STATEMENT = "STATEMENT"
    #: A lump figure the seller entered by hand.
    MANUAL = "MANUAL"


class PayoutStatus(StrEnum):
    """How far reconciliation has got with this payout."""

    #: Recorded, nothing matched yet.
    RECEIVED = "RECEIVED"
    #: Some lines matched, some not.
    PARTIALLY_RECONCILED = "PARTIALLY_RECONCILED"
    #: Every line has an outcome.
    RECONCILED = "RECONCILED"


class PayoutLineStatus(StrEnum):
    """What became of one line of a statement."""

    #: No candidate parcel yet, or none strong enough to apply automatically.
    #: A normal resting state, not a failure (section 81.3).
    UNMATCHED = "UNMATCHED"
    #: Candidates found, none applied. Waiting for the seller.
    SUGGESTED = "SUGGESTED"
    #: Applied automatically on a strong reference.
    MATCHED = "MATCHED"
    #: Applied because a person said so, with a reason (section 81.7).
    MANUAL_MATCHED = "MANUAL_MATCHED"
    #: The same line appears twice in the statement.
    DUPLICATE = "DUPLICATE"
    #: Nothing in this shop's data could correspond to it.
    UNMAPPABLE = "UNMAPPABLE"
    #: Was matched, then unmatched. Section 81.8: reversal, not deletion.
    REVERSED = "REVERSED"
    #: Pays nothing and carries only a charge for a parcel identified by an
    #: exact reference — a return fee, typically. Linked to the parcel so the
    #: charge is explained, but nothing is settled from it.
    CHARGE_ONLY = "CHARGE_ONLY"

    @property
    def is_applied(self) -> bool:
        return self in (PayoutLineStatus.MATCHED, PayoutLineStatus.MANUAL_MATCHED)

    @property
    def is_resolved(self) -> bool:
        """Whether a human needs to look at this line."""
        return self in (
            PayoutLineStatus.MATCHED,
            PayoutLineStatus.MANUAL_MATCHED,
            PayoutLineStatus.DUPLICATE,
            PayoutLineStatus.CHARGE_ONLY,
        )


class MatchConfidence(StrEnum):
    """How sure the engine is (master spec section 16).

    ``MANUAL_REQUIRED`` is not a failure state — it is the correct answer
    whenever the evidence is only an amount, or when two candidates tie.
    Section 112: a reconciliation product optimises precision before recall,
    because missing an auto-match is an inconvenience and wrongly settling
    money is a loss of trust.
    """

    EXACT = "EXACT"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    MANUAL_REQUIRED = "MANUAL_REQUIRED"


class AdjustmentType(StrEnum):
    """What a provider took off, or added (master spec section 84)."""

    COD_FEE = "COD_FEE"
    DELIVERY_FEE = "DELIVERY_FEE"
    RETURN_FEE = "RETURN_FEE"
    TAX = "TAX"
    BONUS = "BONUS"
    PENALTY = "PENALTY"
    MANUAL_ADJUSTMENT = "MANUAL_ADJUSTMENT"
    #: Section 84's closing rule: *"unknown deductions must remain visible, not
    #: silently forced into delivery fee."* This value is how that is honoured.
    UNKNOWN_DEDUCTION = "UNKNOWN_DEDUCTION"


class PayoutSourceFile(Base, TenantOwned, PrimaryKeyMixin):
    """The statement a payout was read from (master spec section 83).

    ``storage_key`` is the Cloudflare R2 object this will live in. R2 is not
    provisioned yet — no bucket, no keys — so the file content is held in
    ``raw_content`` for now and ``storage_key`` stays null. Losing the source
    would break section 81.4, so keeping it somewhere is not optional; where it
    lives is.
    """

    __tablename__ = "payout_source_files"
    __table_args__ = (
        # Re-uploading the same statement is recognised rather than re-imported
        # (section 81.2).
        sa.UniqueConstraint("tenant_id", "sha256", name="uq_payout_source_files_tenant_sha256"),
    )

    provider: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    original_filename: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    sha256: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    size_bytes: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    #: Set once R2 exists. Until then the content is in ``raw_content``.
    storage_key: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)

    #: The statement exactly as uploaded. Section 81.4: reconciliation never
    #: deletes source statement data.
    raw_content: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    imported_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)

    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)


class Payout(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """One payment from a courier, however it reached us."""

    __tablename__ = "payouts"
    __table_args__ = (
        # Section 81.1: every payout has a unique provider identity where one
        # is available. Two rows for one provider reference would double a
        # seller's settled total.
        sa.UniqueConstraint(
            "tenant_id",
            "provider",
            "provider_reference",
            name="uq_payouts_tenant_provider_reference",
        ),
        sa.Index("ix_payouts_tenant_status", "tenant_id", "status"),
        sa.Index("ix_payouts_tenant_paid_on", "tenant_id", "paid_on"),
        sa.CheckConstraint("total_paisa >= 0", name="ck_payouts_total_non_negative"),
    )

    provider: Mapped[str] = mapped_column(sa.String(40), nullable=False)

    #: The provider's own identifier for this payment. Null for a manual entry,
    #: which is why the unique constraint tolerates nulls.
    provider_reference: Mapped[str | None] = mapped_column(sa.String(160), nullable=True)

    source: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        sa.String(24), nullable=False, default=PayoutStatus.RECEIVED
    )

    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default=BDT)

    #: What the provider says it sent, in total.
    total_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)

    #: The sum of what has actually been applied to receivables. Kept next to
    #: the total so the gap between "sent" and "explained" is visible.
    applied_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)

    paid_on: Mapped[date | None] = mapped_column(sa.Date, nullable=True)
    received_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)

    note: Mapped[str | None] = mapped_column(sa.String(400), nullable=True)

    source_file_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID,
        sa.ForeignKey("payout_source_files.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    metadata_json: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)

    lines: Mapped[list[PayoutLine]] = relationship(
        back_populates="payout",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="PayoutLine.row_number",
    )

    @property
    def payout_status(self) -> PayoutStatus:
        return PayoutStatus(self.status)

    @property
    def unexplained_paisa(self) -> int:
        """What arrived but has not been tied to a parcel.

        Shown to the seller as-is. A payout that does not fully explain itself
        is the normal state of a fresh import, and pretending otherwise is how
        an unmatched line disappears.
        """
        return max(0, self.total_paisa - self.applied_paisa)


class PayoutLine(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """One row of a statement: money for one parcel, or one deduction."""

    __tablename__ = "payout_lines"
    __table_args__ = (
        sa.Index("ix_payout_lines_tenant_status", "tenant_id", "status"),
        sa.Index("ix_payout_lines_payout_row", "payout_id", "row_number"),
        # The references the matcher keys off. Indexed because matching a
        # 500-line statement does one lookup per line.
        sa.Index("ix_payout_lines_tenant_consignment_ref", "tenant_id", "provider_consignment_id"),
        sa.Index("ix_payout_lines_tenant_tracking", "tenant_id", "tracking_code"),
        sa.CheckConstraint("amount_paisa >= 0", name="ck_payout_lines_amount_non_negative"),
        sa.CheckConstraint("applied_paisa >= 0", name="ck_payout_lines_applied_non_negative"),
        # Section 17.1: one provider payout line cannot settle more than its
        # own amount. In the database, not only in the service.
        sa.CheckConstraint(
            "applied_paisa <= amount_paisa", name="ck_payout_lines_applied_within_amount"
        ),
    )

    payout_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        sa.ForeignKey("payouts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: Position in the source file, 1-based. Lets support point at a line.
    row_number: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    #: What the line is worth.
    amount_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)

    #: How much of it has been applied to a receivable.
    applied_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)

    status: Mapped[str] = mapped_column(
        sa.String(24), nullable=False, default=PayoutLineStatus.UNMATCHED
    )
    confidence: Mapped[str | None] = mapped_column(sa.String(20), nullable=True)

    #: The references the provider gave us, whichever it gave.
    provider_consignment_id: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)
    tracking_code: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)
    merchant_reference: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)
    customer_phone_last4: Mapped[str | None] = mapped_column(sa.String(4), nullable=True)
    delivered_on: Mapped[date | None] = mapped_column(sa.Date, nullable=True)

    #: The receivable this line was applied to, once it has been.
    receivable_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID,
        sa.ForeignKey("cod_receivables.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    #: Who matched it and why, for a manual match (section 81.7).
    matched_by: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    match_reason: Mapped[str | None] = mapped_column(sa.String(400), nullable=True)
    matched_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    #: The row exactly as it was parsed. Section 83: this is what lets support
    #: explain a reconciliation result later.
    raw: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)

    #: Candidates and their scores from the last matching run, kept so the
    #: seller sees why the engine chose — or refused to choose.
    candidates: Mapped[list] = mapped_column(JSONColumn, nullable=False, default=list)

    payout: Mapped[Payout] = relationship(back_populates="lines")

    @property
    def line_status(self) -> PayoutLineStatus:
        return PayoutLineStatus(self.status)

    @property
    def match_confidence(self) -> MatchConfidence | None:
        return MatchConfidence(self.confidence) if self.confidence else None

    @property
    def unapplied_paisa(self) -> int:
        return max(0, self.amount_paisa - self.applied_paisa)


class PayoutAdjustment(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """A deduction or addition the provider applied (master spec section 84).

    Held separately from the parcel principal (section 81.9) so a seller can
    see what was taken and why. An unrecognised deduction is stored as
    ``UNKNOWN_DEDUCTION`` with the provider's own words in ``raw_text`` — the
    section is explicit that these must stay visible rather than being folded
    into a familiar-looking category.
    """

    __tablename__ = "payout_adjustments"
    __table_args__ = (
        sa.Index("ix_payout_adjustments_tenant_type", "tenant_id", "type"),
        sa.CheckConstraint("amount_paisa >= 0", name="ck_payout_adjustments_amount_non_negative"),
    )

    payout_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        sa.ForeignKey("payouts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    payout_line_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID,
        sa.ForeignKey("payout_lines.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    consignment_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID,
        sa.ForeignKey("consignments.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    type: Mapped[str] = mapped_column(sa.String(24), nullable=False)
    amount_paisa: Mapped[int] = mapped_column(Paisa, nullable=False)

    #: What the provider called it, verbatim.
    provider_label: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    raw_text: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)

    #: Which recognition rule classified it, or null if none did.
    recognized_rule: Mapped[str | None] = mapped_column(sa.String(80), nullable=True)

    #: Set when a person accepted this deduction and it was written to the
    #: ledger. Goes from null to a value once and never back, which is what
    #: makes accepting the same statement's charges twice a no-op.
    accepted_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    accepted_by: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)

    @property
    def is_deduction(self) -> bool:
        """Whether the courier kept this money, as opposed to adding it."""
        return self.adjustment_type is not AdjustmentType.BONUS

    @property
    def adjustment_type(self) -> AdjustmentType:
        return AdjustmentType(self.type)

    @property
    def is_understood(self) -> bool:
        return self.adjustment_type is not AdjustmentType.UNKNOWN_DEDUCTION
