"""The immutable seller financial ledger.

Master spec section 80. This is an append-only *operational* ledger, not a
general ledger and not an accounting product: it records what happened to a
seller's money, in order, so that every balance the app shows can be explained
by pointing at the rows behind it.

The rule that shapes the module is section 80's: **a ledger entry is never
edited.** A correction is a reversal plus a new entry, which means the history
of a mistake survives alongside its fix. Anything that mutated a row in place
would make "why is my COD outstanding ৳4,200?" unanswerable three months later,
and answering that question is the product.

Balances are derived from here (section 80: "user-facing balances are
derived/materialized from ledger + domain tables"), and section 81.10 requires
the dashboard totals to reconcile back to it.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.common.money import BDT
from app.core.clock import utc_now
from app.db.base import Base, PrimaryKeyMixin, TenantOwned
from app.db.types import GUID, JSONColumn, Paisa, TZDateTime

__all__ = [
    "LedgerBucket",
    "LedgerDirection",
    "LedgerEntry",
    "LedgerEventType",
    "LedgerSource",
]


class LedgerBucket(StrEnum):
    """Which pot of money an entry belongs to.

    The names are section 80's, used verbatim. The value is stored, so renaming
    one would rewrite the meaning of historical rows.
    """

    #: Money a courier owes the seller for delivered parcels.
    COD_RECEIVABLE = "COD_RECEIVABLE"
    #: Money that actually arrived.
    COD_SETTLED = "COD_SETTLED"
    #: Delivery charges taken by the courier.
    COURIER_CHARGE = "COURIER_CHARGE"
    #: What a return costs — often charged even though nothing was collected.
    RETURN_CHARGE = "RETURN_CHARGE"
    #: The courier's fee for handling cash.
    COD_FEE = "COD_FEE"
    #: A correction, a provider bonus, a penalty, or anything the seller entered
    #: by hand with a reason.
    ADJUSTMENT = "ADJUSTMENT"
    #: Money given up as uncollectable.
    WRITE_OFF = "WRITE_OFF"
    #: Money returned to a customer.
    REFUND = "REFUND"
    #: A deduction the provider applied that we could not classify. Section 84
    #: is explicit that these stay visible rather than being folded into
    #: "delivery fee" to make the arithmetic tidy.
    OTHER = "OTHER"


class LedgerDirection(StrEnum):
    """Which way the money moved, from the seller's point of view."""

    #: Money owed to, or received by, the seller.
    CREDIT = "CREDIT"
    #: Money leaving the seller, or a receivable being cleared.
    DEBIT = "DEBIT"


class LedgerEventType(StrEnum):
    """What happened.

    Deliberately concrete. A generic "amount changed" event would record the
    number without the reason, and the reason is what a seller is actually
    asking for when they open the Money screen.
    """

    #: A parcel was delivered and its collectible amount became a receivable.
    DELIVERY_CONFIRMED = "DELIVERY_CONFIRMED"
    #: Some units were delivered; only those are collectible (section 17.8).
    PARTIAL_DELIVERY_CONFIRMED = "PARTIAL_DELIVERY_CONFIRMED"
    #: A parcel came back. Nothing is collectible, and a charge may still apply.
    RETURN_CONFIRMED = "RETURN_CONFIRMED"
    #: A payout line was applied against a receivable.
    PAYOUT_APPLIED = "PAYOUT_APPLIED"
    #: A payout application was undone. Section 81.8: unmatching is a reversal,
    #: never a destructive overwrite.
    PAYOUT_REVERSED = "PAYOUT_REVERSED"
    #: A provider deduction recognised from a statement.
    PROVIDER_DEDUCTION = "PROVIDER_DEDUCTION"
    #: The courier's delivery charge.
    COURIER_CHARGE_APPLIED = "COURIER_CHARGE_APPLIED"
    #: A return fee.
    RETURN_FEE_APPLIED = "RETURN_FEE_APPLIED"
    #: The COD handling fee.
    COD_FEE_APPLIED = "COD_FEE_APPLIED"
    #: A seller correction, made through the section 134 workflow.
    MANUAL_ADJUSTMENT = "MANUAL_ADJUSTMENT"
    #: A receivable given up.
    WRITE_OFF = "WRITE_OFF"
    #: A reversal of any of the above.
    REVERSAL = "REVERSAL"


class LedgerSource(StrEnum):
    """Where the entry came from."""

    #: A courier status event, webhook or poll.
    PROVIDER_EVENT = "PROVIDER_EVENT"
    #: A payout statement or payment API.
    PAYOUT = "PAYOUT"
    #: The seller, by hand.
    SELLER = "SELLER"
    #: The reconciliation engine.
    RECONCILIATION = "RECONCILIATION"
    #: A background job.
    SYSTEM = "SYSTEM"


#: Which direction each event type moves money in, and which bucket it lands
#: in. Keeping this as data rather than as branches at call sites is what makes
#: it testable: a single test walks every event type and asserts the pairing,
#: so a new event cannot be added without deciding both.
EVENT_SHAPE: dict[LedgerEventType, tuple[LedgerDirection, LedgerBucket]] = {
    LedgerEventType.DELIVERY_CONFIRMED: (
        LedgerDirection.CREDIT,
        LedgerBucket.COD_RECEIVABLE,
    ),
    LedgerEventType.PARTIAL_DELIVERY_CONFIRMED: (
        LedgerDirection.CREDIT,
        LedgerBucket.COD_RECEIVABLE,
    ),
    LedgerEventType.RETURN_CONFIRMED: (
        LedgerDirection.DEBIT,
        LedgerBucket.COD_RECEIVABLE,
    ),
    LedgerEventType.PAYOUT_APPLIED: (LedgerDirection.CREDIT, LedgerBucket.COD_SETTLED),
    LedgerEventType.PAYOUT_REVERSED: (LedgerDirection.DEBIT, LedgerBucket.COD_SETTLED),
    LedgerEventType.PROVIDER_DEDUCTION: (LedgerDirection.DEBIT, LedgerBucket.OTHER),
    LedgerEventType.COURIER_CHARGE_APPLIED: (
        LedgerDirection.DEBIT,
        LedgerBucket.COURIER_CHARGE,
    ),
    LedgerEventType.RETURN_FEE_APPLIED: (
        LedgerDirection.DEBIT,
        LedgerBucket.RETURN_CHARGE,
    ),
    LedgerEventType.COD_FEE_APPLIED: (LedgerDirection.DEBIT, LedgerBucket.COD_FEE),
    LedgerEventType.MANUAL_ADJUSTMENT: (LedgerDirection.CREDIT, LedgerBucket.ADJUSTMENT),
    LedgerEventType.WRITE_OFF: (LedgerDirection.DEBIT, LedgerBucket.WRITE_OFF),
    LedgerEventType.REVERSAL: (LedgerDirection.DEBIT, LedgerBucket.ADJUSTMENT),
}


class LedgerEntry(Base, TenantOwned, PrimaryKeyMixin):
    """One immutable line in a shop's money history.

    There is deliberately no ``updated_at`` and no update path in
    :mod:`app.ledger.service`. The absence is the invariant.
    """

    __tablename__ = "financial_ledger_entries"
    __table_args__ = (
        # The Money screen reads a shop's history newest-first, and the
        # reconciliation reports read it per bucket over a date range.
        sa.Index("ix_ledger_tenant_occurred", "tenant_id", "occurred_at"),
        sa.Index("ix_ledger_tenant_bucket_date", "tenant_id", "bucket", "business_date"),
        sa.Index("ix_ledger_tenant_entity", "tenant_id", "entity_type", "entity_id"),
        # An amount is always positive; ``direction`` carries the sign. Storing
        # a signed amount as well would let the two disagree.
        sa.CheckConstraint("amount_paisa >= 0", name="ck_ledger_amount_non_negative"),
    )

    #: When the money event happened, in UTC.
    occurred_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utc_now, index=True
    )

    #: The Asia/Dhaka day it belongs to. Money is reported on the seller's
    #: calendar, not on UTC's (master spec section 69).
    business_date: Mapped[date] = mapped_column(sa.Date, nullable=False, index=True)

    #: What the entry is about: ``consignment``, ``payout_line``, ``order``…
    entity_type: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False)

    event_type: Mapped[str] = mapped_column(sa.String(40), nullable=False)

    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default=BDT)

    #: Always positive. See the check constraint above.
    amount_paisa: Mapped[int] = mapped_column(Paisa, nullable=False)

    direction: Mapped[str] = mapped_column(sa.String(8), nullable=False)
    bucket: Mapped[str] = mapped_column(sa.String(24), nullable=False)

    source: Mapped[str] = mapped_column(sa.String(24), nullable=False)

    #: A provider reference, a statement line number, a job id — whatever lets
    #: support trace this row back to the thing that caused it.
    source_ref: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)

    #: Set on a reversal, pointing at the entry it undoes. Section 80: a
    #: correction is a reversal plus a new entry, never an edit.
    reversal_of: Mapped[uuid.UUID | None] = mapped_column(
        GUID,
        sa.ForeignKey("financial_ledger_entries.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    #: Why, for a manual correction. Section 80 requires actor, reason and an
    #: audit event on every one.
    reason: Mapped[str | None] = mapped_column(sa.String(400), nullable=True)

    #: Who caused it, when a person did.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)

    metadata_json: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utc_now)

    @property
    def ledger_bucket(self) -> LedgerBucket:
        return LedgerBucket(self.bucket)

    @property
    def ledger_direction(self) -> LedgerDirection:
        return LedgerDirection(self.direction)

    @property
    def signed_paisa(self) -> int:
        """The amount with its direction applied.

        Only for summing. It is not stored, because a stored signed amount and
        a stored direction are two representations of one fact that can drift
        apart.
        """
        if self.ledger_direction is LedgerDirection.CREDIT:
            return self.amount_paisa
        return -self.amount_paisa
