"""Charge snapshots and profit snapshots.

Master spec sections 18, 19 and 85. Two immutability rules shape this module,
both from section 17:

* **17.3** — historical charge snapshots are immutable except via audited
  correction;
* **17.4** — a profit snapshot change requires a new revision and an audit
  event.

So neither table is edited in place. A charge that turns out to be wrong gets a
superseding row; a profit figure that needs recalculating gets a new revision
with the old one kept. Section 55's acceptance criterion is the reason: *"old
order profit does not change after new rate rule."* A seller who reconciled
last month's numbers must be able to open them next year and see the same
figures.
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
    "CALCULATION_VERSION",
    "ChargeKind",
    "ChargeSource",
    "ConsignmentCharge",
    "ProfitQuality",
    "ProfitSnapshot",
    "ReturnReason",
]


#: Bumped whenever the profit formula itself changes. Stored on every snapshot,
#: so a figure calculated under an older rule is identifiable rather than
#: silently mixed with newer ones (section 18.2).
CALCULATION_VERSION = 1


class ChargeKind(StrEnum):
    """What a charge is for.

    These are the subtractions in section 18.1's formula, one enum value each.
    Keeping them separate rather than as a single "fees" total is what lets the
    Insights screen say *where* the money went.
    """

    #: The courier's delivery charge.
    DELIVERY = "DELIVERY"
    #: The courier's fee for handling cash.
    COD_FEE = "COD_FEE"
    #: Charged on a parcel that came back, often with nothing collected.
    RETURN = "RETURN"
    #: Boxes, tape, labels. Seller-entered or configured.
    PACKAGING = "PACKAGING"
    #: A payment gateway's cut, where one is involved.
    PAYMENT_FEE = "PAYMENT_FEE"
    #: Anything else that varies with the parcel.
    OTHER = "OTHER"


class ChargeSource(StrEnum):
    """Where a charge figure came from.

    This is master spec section 85's truth hierarchy, in order. The order
    matters: :meth:`ChargeSource.rank` uses it to work out how trustworthy a
    whole profit figure is, and section 85's closing line — *"do not show an
    estimated profit as exact without a marker"* — is enforced from that.
    """

    #: The provider's own settled figure, off a statement.
    SETTLED = "SETTLED"
    #: What the provider quoted at booking.
    BOOKED = "BOOKED"
    #: The seller typed the actual amount.
    SELLER = "SELLER"
    #: A configured default for this shop.
    ESTIMATE = "ESTIMATE"
    #: Nobody knows. Recorded rather than assumed to be zero.
    UNKNOWN = "UNKNOWN"

    @property
    def rank(self) -> int:
        """Position in the truth hierarchy. Lower is better."""
        return _SOURCE_RANK[self]

    @property
    def is_actual(self) -> bool:
        """Whether this figure is a measured value rather than a guess."""
        return self in (ChargeSource.SETTLED, ChargeSource.SELLER)


_SOURCE_RANK: dict[ChargeSource, int] = {
    ChargeSource.SETTLED: 0,
    ChargeSource.BOOKED: 1,
    ChargeSource.SELLER: 2,
    ChargeSource.ESTIMATE: 3,
    ChargeSource.UNKNOWN: 4,
}


class ProfitQuality(StrEnum):
    """How much a profit figure can be trusted (master spec section 135).

    The UI shows this next to the number. Section 85: an estimated profit must
    never be presented as exact, and this is the field that stops it.
    """

    #: Every input is a measured value.
    ACTUAL = "ACTUAL"
    #: Some input is a quote, a configured default, or not known at all. The
    #: figure is usable but not exact, and the gaps are named alongside it.
    ESTIMATED = "ESTIMATED"
    #: Revenue or product cost is unknown, which makes the figure meaningless
    #: rather than merely imprecise. Saying so beats a confident wrong number.
    MISSING = "MISSING"

    @classmethod
    def from_sources(
        cls,
        *,
        essential: list[ChargeSource],
        peripheral: list[ChargeSource] | None = None,
    ) -> ProfitQuality:
        """Rate a figure by its worst input, weighing the essentials heavier.

        The split follows section 135's own example — *"Profit ৳333 —
        **Estimated** (ad cost missing)"*. A missing ad cost leaves a figure
        that is merely optimistic; the seller can still act on it, and the gap
        is named next to it. A missing *product cost* leaves nothing worth
        showing at all, because profit is revenue minus cost and one of those
        terms is absent.

        So an unknown among the essentials (revenue, item cost) is ``MISSING``;
        an unknown anywhere else is ``ESTIMATED``.
        """
        peripheral = peripheral or []
        if any(source is ChargeSource.UNKNOWN for source in essential):
            return cls.MISSING

        worst = max(
            (source.rank for source in [*essential, *peripheral]),
            default=ChargeSource.SETTLED.rank,
        )
        if worst > ChargeSource.SELLER.rank:
            return cls.ESTIMATED
        if worst == ChargeSource.BOOKED.rank:
            # A booking quote is what the provider *said* it would charge,
            # which is regularly not what it charges.
            return cls.ESTIMATED
        return cls.ACTUAL


class ReturnReason(StrEnum):
    """Why a parcel came back (master spec section 19).

    The enum is the section's initial list, verbatim. Its closing instruction
    governs the wording: *"use 'risk' wording, not defamatory person labels."*
    Every value here describes what happened to the parcel, never what kind of
    person the customer is.
    """

    CUSTOMER_UNREACHABLE = "CUSTOMER_UNREACHABLE"
    CUSTOMER_REFUSED = "CUSTOMER_REFUSED"
    WRONG_PRODUCT = "WRONG_PRODUCT"
    WRONG_SIZE = "WRONG_SIZE"
    DAMAGED = "DAMAGED"
    DELAYED_DELIVERY = "DELAYED_DELIVERY"
    CHANGED_MIND = "CHANGED_MIND"
    #: Section 19's own wording. "Suspicion" about an *order*, not a verdict
    #: about a person (section 130).
    SUSPECTED_INVALID_ORDER = "SUSPECTED_INVALID_ORDER"
    COURIER_ISSUE = "COURIER_ISSUE"
    MERCHANT_ISSUE = "MERCHANT_ISSUE"
    OTHER = "OTHER"

    @property
    def is_merchant_fault(self) -> bool:
        """Whether the shop caused it.

        Used only to group the returns report so a seller can see which losses
        they can act on. It assigns no blame to anybody outside the shop.
        """
        return self in (
            ReturnReason.WRONG_PRODUCT,
            ReturnReason.WRONG_SIZE,
            ReturnReason.MERCHANT_ISSUE,
        )


class ConsignmentCharge(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """One cost attached to one parcel, as it was known at a point in time.

    Append-only. A charge that changes — a statement arriving with the real
    delivery fee after a booking estimate — is recorded as a *new* row that
    supersedes the old one, so the estimate and the settled figure both survive
    (section 17.3).
    """

    __tablename__ = "consignment_charges"
    __table_args__ = (
        sa.Index("ix_consignment_charges_tenant_kind", "tenant_id", "kind"),
        sa.Index(
            "ix_consignment_charges_consignment_current",
            "consignment_id",
            "superseded_by",
        ),
        sa.CheckConstraint("amount_paisa >= 0", name="ck_consignment_charges_amount_non_negative"),
    )

    consignment_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        sa.ForeignKey("consignments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    kind: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    source: Mapped[str] = mapped_column(sa.String(16), nullable=False)

    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default=BDT)
    amount_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)

    #: What the provider called it, when a provider supplied it.
    provider_label: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)

    #: Where the figure came from: a payout adjustment, a rate card, the seller.
    source_ref: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)

    #: Set when a better figure arrives. The superseded row stays exactly as it
    #: was written — this column is the only thing that changes on it, and it
    #: only ever goes from null to a value.
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID,
        sa.ForeignKey("consignment_charges.id", ondelete="RESTRICT"),
        nullable=True,
    )

    #: Why a correction was made. Required when superseding by hand
    #: (section 17.3: "immutable except via audited correction").
    reason: Mapped[str | None] = mapped_column(sa.String(400), nullable=True)

    occurred_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)

    metadata_json: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)

    @property
    def charge_kind(self) -> ChargeKind:
        return ChargeKind(self.kind)

    @property
    def charge_source(self) -> ChargeSource:
        return ChargeSource(self.source)

    @property
    def is_current(self) -> bool:
        return self.superseded_by is None


class ProfitSnapshot(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """What one parcel actually earned, frozen at a moment.

    Section 18.2's field list, plus the revision machinery section 17.4
    requires. A recalculation writes a new row with ``revision + 1`` and marks
    the previous one superseded; the old figures stay readable forever.
    """

    __tablename__ = "profit_snapshots"
    __table_args__ = (
        # One current snapshot per parcel per revision.
        sa.UniqueConstraint(
            "tenant_id",
            "consignment_id",
            "revision",
            name="uq_profit_snapshots_tenant_consignment_revision",
        ),
        sa.Index("ix_profit_snapshots_tenant_date", "tenant_id", "business_date"),
        sa.Index("ix_profit_snapshots_tenant_current", "tenant_id", "is_current"),
        sa.Index("ix_profit_snapshots_order", "order_id"),
    )

    consignment_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        sa.ForeignKey("consignments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )

    #: The Asia/Dhaka day the parcel settled, for period reporting.
    business_date: Mapped[date] = mapped_column(sa.Date, nullable=False)

    revision: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    is_current: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID,
        sa.ForeignKey("profit_snapshots.id", ondelete="RESTRICT"),
        nullable=True,
    )

    #: Which version of the formula produced this.
    calculation_version: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=CALCULATION_VERSION
    )

    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default=BDT)

    # --- section 18.1's terms, one column each -----------------------------

    #: What was actually collected from the customer. For a partial delivery,
    #: the delivered units only — never the order total.
    realized_revenue_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)

    #: Cost of the units that stayed with the customer. Units that came back
    #: sellable are not a cost: they are back on the shelf.
    item_cost_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)

    delivery_charge_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)
    cod_fee_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)
    return_charge_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)
    packaging_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)
    payment_fee_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)
    other_cost_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)

    #: Advertising allocated to this parcel. Section 86 requires the method to
    #: be explicit, so it is stored next to the figure.
    ad_cost_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)
    allocation_method: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)
    allocation_version: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)

    #: The discount already reflected in ``realized_revenue_paisa``. A memo, not
    #: a subtraction — see :func:`app.profit.engine.contribution_profit`.
    discount_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)

    #: Cost of units written off rather than restored to stock.
    write_off_cost_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)

    #: The bottom line.
    contribution_profit_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)

    #: Margin against realized revenue, in basis points (100 = 1%). Null when
    #: nothing was collected — a return has a loss but no margin, and 0% would
    #: read as break-even.
    margin_basis_points: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)

    quality: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, default=ProfitQuality.ACTUAL
    )

    #: Which inputs were not measured, so the UI can say *what* is missing
    #: rather than only that something is (section 135's "Estimated (ad cost
    #: missing)").
    missing_inputs: Mapped[list] = mapped_column(JSONColumn, nullable=False, default=list)

    #: Why this revision exists, when a person caused it.
    reason: Mapped[str | None] = mapped_column(sa.String(400), nullable=True)

    #: How the parcel ended, and why if it came back.
    outcome: Mapped[str] = mapped_column(sa.String(24), nullable=False)
    return_reason: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)

    calculated_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)

    metadata_json: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)

    @property
    def profit_quality(self) -> ProfitQuality:
        return ProfitQuality(self.quality)

    @property
    def is_loss(self) -> bool:
        return self.contribution_profit_paisa < 0

    @property
    def total_cost_paisa(self) -> int:
        """Everything subtracted from revenue."""
        return (
            self.item_cost_paisa
            + self.delivery_charge_paisa
            + self.cod_fee_paisa
            + self.return_charge_paisa
            + self.packaging_paisa
            + self.payment_fee_paisa
            + self.other_cost_paisa
            + self.ad_cost_paisa
            + self.write_off_cost_paisa
        )
