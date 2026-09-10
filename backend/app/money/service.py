"""COD receivables — the money a courier is holding.

Every change to a receivable writes a ledger entry in the same transaction
(master spec section 80), and no method here edits a settled total directly:
settlement is applied through :meth:`ReceivableService.apply_settlement` and
undone through :meth:`ReceivableService.reverse_settlement`, which is a
reversal rather than a subtraction (section 81.8).

The single most important line in this module is the guard in
``apply_settlement``: one payout line cannot settle more than the receivable
is owed (section 17.2). Everything else is bookkeeping around that.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import AuditAction, record_audit
from app.common.pagination import Cursor, apply_cursor
from app.consignments.models import Consignment
from app.core.clock import business_date, utc_now
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.ledger.models import (
    LedgerBucket,
    LedgerDirection,
    LedgerEventType,
    LedgerSource,
)
from app.ledger.service import LedgerService
from app.money.models import CodReceivable, ReceivableStatus, can_transition
from app.orders.models import Order
from app.profit.service import ProfitService

__all__ = ["AGING_BUCKETS", "AgingBucket", "ReceivableService"]


@dataclass(frozen=True, slots=True)
class AgingBucket:
    """One band of the COD aging report."""

    label: str
    min_days: int
    #: ``None`` means open-ended.
    max_days: int | None

    def contains(self, days: int) -> bool:
        if days < self.min_days:
            return False
        return self.max_days is None or days <= self.max_days


#: The aging bands the Money screen shows. Days, not weeks, because the
#: difference between four days and eight is the difference between "normal"
#: and "chase this" for a Bangladeshi courier.
AGING_BUCKETS: tuple[AgingBucket, ...] = (
    AgingBucket(label="0-3 days", min_days=0, max_days=3),
    AgingBucket(label="4-7 days", min_days=4, max_days=7),
    AgingBucket(label="8-14 days", min_days=8, max_days=14),
    AgingBucket(label="15+ days", min_days=15, max_days=None),
)


class ReceivableService:
    """The only writer of COD receivables."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        ledger: LedgerService | None = None,
        profit: ProfitService | None = None,
    ) -> None:
        self._db = session
        self._ledger = ledger or LedgerService(session)
        self._profit = profit or ProfitService(session)

    # ----------------------------------------------------------- lifecycle --

    async def open_for(
        self, consignment: Consignment, *, order: Order | None = None
    ) -> CodReceivable:
        """Create the receivable for a dispatched parcel, in ``EXPECTED``.

        Nothing is collectible yet and no ledger entry is written: the courier
        does not owe anything until they have collected it. Writing a
        receivable credit at dispatch would show a seller money they might
        never see, for every parcel in transit.
        """
        existing = await self.for_consignment(consignment.id)
        if existing is not None:
            return existing

        receivable = CodReceivable(
            consignment_id=consignment.id,
            order_id=consignment.order_id,
            provider=consignment.provider,
            status=str(ReceivableStatus.EXPECTED),
            collectible_paisa=0,
            metadata_json={
                "order_number": order.order_number if order else None,
                "expected_cod_paisa": consignment.cod_amount_paisa,
            },
        )
        self._db.add(receivable)
        await self._db.flush()
        return receivable

    async def mark_eligible(
        self,
        consignment: Consignment,
        *,
        collected_at: datetime | None = None,
        note: str | None = None,
    ) -> CodReceivable:
        """A parcel was delivered: the money is now collectible.

        The amount comes from ``Consignment.collectible_paisa``, which counts
        delivered units only. Section 17.8: a partial delivery creates a partial
        collectible amount and never assumes the original COD.
        """
        receivable = await self._require(consignment.id)
        collectible = consignment.collectible_paisa
        moment = collected_at or utc_now()

        if collectible <= 0:
            # Delivered nothing worth collecting — a zero-COD parcel, or a
            # partial where every delivered line was free.
            return await self.mark_not_due(
                consignment, reason=note or "Nothing collectible", occurred_at=moment
            )

        previous = receivable.collectible_paisa
        self._transition(receivable, ReceivableStatus.ELIGIBLE)
        receivable.collectible_paisa = collectible
        receivable.eligible_at = moment
        receivable.eligible_business_date = business_date(at=moment)
        receivable.status_reason = note
        receivable.version += 1
        await self._db.flush()

        # The ledger records the *change*, so re-reporting a delivery with a
        # corrected amount adds the difference rather than double-counting.
        delta = collectible - previous
        if delta != 0:
            await self._ledger.record(
                event_type=(
                    LedgerEventType.PARTIAL_DELIVERY_CONFIRMED
                    if collectible < consignment.cod_amount_paisa
                    else LedgerEventType.DELIVERY_CONFIRMED
                ),
                entity_type="consignment",
                entity_id=consignment.id,
                amount_paisa=abs(delta),
                # A corrected-down amount debits the difference back out.
                direction=(LedgerDirection.CREDIT if delta > 0 else LedgerDirection.DEBIT),
                source=LedgerSource.PROVIDER_EVENT,
                occurred_at=moment,
                source_ref=consignment.merchant_reference,
                metadata={
                    "parcel_cod_paisa": consignment.cod_amount_paisa,
                    "collectible_paisa": collectible,
                },
            )
        return receivable

    async def mark_not_due(
        self,
        consignment: Consignment,
        *,
        reason: str,
        occurred_at: datetime | None = None,
    ) -> CodReceivable:
        """A parcel came back, or was cancelled: nothing is collectible.

        If the receivable had already been made eligible, the credit is
        reversed rather than erased — the delivery genuinely happened and the
        history should say so.
        """
        receivable = await self._require(consignment.id)
        moment = occurred_at or utc_now()

        if receivable.collectible_paisa > 0:
            await self._ledger.record(
                event_type=LedgerEventType.RETURN_CONFIRMED,
                entity_type="consignment",
                entity_id=consignment.id,
                amount_paisa=receivable.collectible_paisa,
                source=LedgerSource.PROVIDER_EVENT,
                occurred_at=moment,
                source_ref=consignment.merchant_reference,
                reason=reason,
            )

        self._transition(receivable, ReceivableStatus.NOT_DUE)
        receivable.collectible_paisa = 0
        receivable.eligible_at = None
        receivable.eligible_business_date = None
        receivable.status_reason = reason
        receivable.version += 1
        await self._db.flush()
        return receivable

    # ---------------------------------------------------------- settlement --

    async def apply_settlement(
        self,
        receivable_id: uuid.UUID,
        *,
        amount_paisa: int,
        source_ref: str,
        payout_line_id: uuid.UUID | None = None,
        occurred_at: datetime | None = None,
        tolerance_paisa: int = 0,
    ) -> CodReceivable:
        """Apply money that arrived against what is owed.

        Refuses to settle more than the receivable is owed. Master spec section
        17.2: the settled total cannot exceed the collectible net without an
        explicit adjustment. An overpayment is a reconciliation case for a human
        to look at, not something to absorb silently — silently absorbing it is
        how a provider's error becomes the seller's loss.
        """
        receivable = await self._get(receivable_id)
        if amount_paisa <= 0:
            raise ValidationError("A settlement must be a positive amount")

        if not receivable.receivable_status.is_collectible:
            raise ConflictError(
                "That receivable is not collectible",
                details={"status": receivable.status},
            )

        remaining = receivable.expected_paisa - receivable.settled_paisa
        if amount_paisa > remaining + tolerance_paisa:
            raise ConflictError(
                "That payment is more than this parcel is owed",
                details={
                    "outstanding_paisa": remaining,
                    "offered_paisa": amount_paisa,
                    "tolerance_paisa": tolerance_paisa,
                },
            )

        moment = occurred_at or utc_now()
        receivable.settled_paisa += amount_paisa
        receivable.version += 1

        if receivable.is_fully_settled:
            self._transition(receivable, ReceivableStatus.SETTLED)
            receivable.settled_at = moment
        else:
            self._transition(receivable, ReceivableStatus.PARTIALLY_SETTLED)
        await self._db.flush()

        # Two entries, because the money left one bucket and entered another:
        # the receivable shrinks and the settled pot grows. Section 80 shows
        # exactly this pair.
        await self._ledger.record(
            event_type=LedgerEventType.PAYOUT_APPLIED,
            entity_type="cod_receivable",
            entity_id=receivable.id,
            amount_paisa=amount_paisa,
            bucket=LedgerBucket.COD_RECEIVABLE,
            direction=LedgerDirection.DEBIT,
            source=LedgerSource.PAYOUT,
            occurred_at=moment,
            source_ref=source_ref,
            metadata={"payout_line_id": str(payout_line_id) if payout_line_id else None},
        )
        await self._ledger.record(
            event_type=LedgerEventType.PAYOUT_APPLIED,
            entity_type="cod_receivable",
            entity_id=receivable.id,
            amount_paisa=amount_paisa,
            source=LedgerSource.PAYOUT,
            occurred_at=moment,
            source_ref=source_ref,
            metadata={"payout_line_id": str(payout_line_id) if payout_line_id else None},
        )

        # Money arriving changes what the parcel earned *and* how sure we are
        # of it: revenue moves from BOOKED to SETTLED (section 85), and any
        # deduction the payout carried is now a known charge rather than an
        # estimate. Without this the Insights screen would keep showing the
        # figure it computed on the day of delivery, and the quality indicator
        # would never reach "actual" for anything.
        await self._resnapshot(receivable, reason="Payout applied")
        return receivable

    async def _resnapshot(self, receivable: CodReceivable, *, reason: str) -> None:
        """Write a new profit revision for the parcel behind a receivable.

        Silent when the parcel has no snapshot yet — a receivable can be
        settled for a parcel whose outcome predates profit snapshots, and
        refusing the settlement over that would block the money for a
        bookkeeping reason.
        """
        if await self._profit.current_snapshot(receivable.consignment_id) is None:
            return
        await self._profit.snapshot(receivable.consignment_id, reason=reason)

    async def reverse_settlement(
        self,
        receivable_id: uuid.UUID,
        *,
        amount_paisa: int,
        reason: str,
        source_ref: str,
    ) -> CodReceivable:
        """Undo a settlement.

        Section 81.8: unmatching is implemented as a reversal, not a
        destructive overwrite. The original ledger entries stay; two more
        appear undoing them.
        """
        receivable = await self._get(receivable_id)
        if amount_paisa <= 0:
            raise ValidationError("A reversal must be a positive amount")
        if amount_paisa > receivable.settled_paisa:
            raise ConflictError(
                "That is more than has been settled against this parcel",
                details={
                    "settled_paisa": receivable.settled_paisa,
                    "requested_paisa": amount_paisa,
                },
            )

        now = utc_now()
        receivable.settled_paisa -= amount_paisa
        receivable.settled_at = None
        receivable.version += 1
        self._transition(
            receivable,
            ReceivableStatus.PARTIALLY_SETTLED
            if receivable.settled_paisa > 0
            else ReceivableStatus.ELIGIBLE,
        )
        await self._db.flush()

        # Mirror image of `apply_settlement`: the receivable is credited back
        # and the settled pot is debited. The event's declared shape is the
        # settled half, so the receivable half states its direction explicitly.
        await self._ledger.record(
            event_type=LedgerEventType.PAYOUT_REVERSED,
            entity_type="cod_receivable",
            entity_id=receivable.id,
            amount_paisa=amount_paisa,
            bucket=LedgerBucket.COD_RECEIVABLE,
            direction=LedgerDirection.CREDIT,
            source=LedgerSource.RECONCILIATION,
            occurred_at=now,
            source_ref=source_ref,
            reason=reason,
        )
        await self._ledger.record(
            event_type=LedgerEventType.PAYOUT_REVERSED,
            entity_type="cod_receivable",
            entity_id=receivable.id,
            amount_paisa=amount_paisa,
            source=LedgerSource.RECONCILIATION,
            occurred_at=now,
            source_ref=source_ref,
            reason=reason,
        )
        await record_audit(
            self._db,
            action=AuditAction.RECONCILIATION_UNMATCHED,
            entity_type="cod_receivable",
            entity_id=receivable.id,
            reason=reason,
            context={"amount_paisa": amount_paisa},
        )
        return receivable

    async def record_deduction(
        self,
        receivable_id: uuid.UUID,
        *,
        amount_paisa: int,
        event_type: LedgerEventType,
        source_ref: str,
        label: str | None = None,
        occurred_at: datetime | None = None,
    ) -> CodReceivable:
        """Record what the provider took out of the parcel's money.

        Deductions are held separately from the principal so a seller can see
        what the courier charged. Section 84: an unrecognised deduction stays
        visible as ``OTHER`` rather than being folded into "delivery fee" to
        make the arithmetic tidy.
        """
        receivable = await self._get(receivable_id)
        if amount_paisa <= 0:
            raise ValidationError("A deduction must be a positive amount")

        receivable.deduction_paisa += amount_paisa
        receivable.version += 1

        # A deduction can close the gap on its own: ৳1,325 paid against ৳1,405
        # owed is fully settled once the ৳80 COD fee is recognised. Leaving it
        # PARTIALLY_SETTLED would keep a fully-explained parcel sitting in the
        # seller's "waiting" list forever showing nothing outstanding.
        if (
            receivable.is_fully_settled
            and receivable.receivable_status is not ReceivableStatus.SETTLED
        ):
            self._transition(receivable, ReceivableStatus.SETTLED)
            receivable.settled_at = occurred_at or utc_now()
        await self._db.flush()

        await self._ledger.record(
            event_type=event_type,
            entity_type="cod_receivable",
            entity_id=receivable.id,
            amount_paisa=amount_paisa,
            source=LedgerSource.PAYOUT,
            occurred_at=occurred_at or utc_now(),
            source_ref=source_ref,
            metadata={"provider_label": label},
        )
        return receivable

    # --------------------------------------------------------- seller acts --

    async def mark_mismatched(self, receivable_id: uuid.UUID, *, reason: str) -> CodReceivable:
        receivable = await self._get(receivable_id)
        self._transition(receivable, ReceivableStatus.MISMATCHED)
        receivable.status_reason = reason
        receivable.version += 1
        await self._db.flush()
        return receivable

    async def mark_disputed(self, receivable_id: uuid.UUID, *, reason: str) -> CodReceivable:
        receivable = await self._get(receivable_id)
        self._transition(receivable, ReceivableStatus.DISPUTED)
        receivable.status_reason = reason
        receivable.version += 1
        await self._db.flush()
        return receivable

    async def write_off(self, receivable_id: uuid.UUID, *, reason: str) -> CodReceivable:
        """Give up on collecting.

        Terminal, audited, and it writes the outstanding amount to the
        ``WRITE_OFF`` bucket so the loss is visible in the ledger rather than
        the receivable simply vanishing from the outstanding total.
        """
        receivable = await self._get(receivable_id)
        outstanding = receivable.outstanding_paisa
        self._transition(receivable, ReceivableStatus.WRITTEN_OFF)
        receivable.status_reason = reason
        receivable.version += 1
        await self._db.flush()

        if outstanding > 0:
            await self._ledger.record(
                event_type=LedgerEventType.WRITE_OFF,
                entity_type="cod_receivable",
                entity_id=receivable.id,
                amount_paisa=outstanding,
                source=LedgerSource.SELLER,
                reason=reason,
            )
        await record_audit(
            self._db,
            action=AuditAction.RECEIVABLE_WRITTEN_OFF,
            entity_type="cod_receivable",
            entity_id=receivable.id,
            reason=reason,
            context={"amount_paisa": outstanding},
        )
        return receivable

    # ------------------------------------------------------------- reading --

    async def get(self, receivable_id: uuid.UUID) -> CodReceivable:
        return await self._get(receivable_id)

    async def for_consignment(self, consignment_id: uuid.UUID) -> CodReceivable | None:
        result = await self._db.execute(
            sa.select(CodReceivable).where(CodReceivable.consignment_id == consignment_id)
        )
        return result.scalar_one_or_none()

    async def list_receivables(
        self,
        *,
        limit: int = 30,
        cursor: Cursor | None = None,
        status: ReceivableStatus | None = None,
        provider: str | None = None,
        open_only: bool = False,
    ) -> list[CodReceivable]:
        stmt = sa.select(CodReceivable)
        if status is not None:
            stmt = stmt.where(CodReceivable.status == str(status))
        if provider is not None:
            stmt = stmt.where(CodReceivable.provider == provider)
        if open_only:
            stmt = stmt.where(
                CodReceivable.status.in_(
                    [str(value) for value in ReceivableStatus if value.is_collectible]
                )
            )
        stmt = apply_cursor(stmt, CodReceivable, cursor)
        stmt = stmt.order_by(CodReceivable.created_at.desc(), CodReceivable.id.desc()).limit(
            limit + 1
        )
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def aging(
        self, *, as_of: datetime | None = None, provider: str | None = None
    ) -> list[tuple[AgingBucket, int, int]]:
        """Outstanding money grouped by how long it has been waiting.

        Returns ``(bucket, parcel_count, outstanding_paisa)``. Computed in
        Python rather than SQL because the outstanding amount depends on
        ``expected − settled − deductions``, and expressing that as a portable
        SQL expression across PostgreSQL and SQLite buys nothing at these row
        counts.
        """
        moment = as_of or utc_now()
        stmt = sa.select(CodReceivable).where(
            CodReceivable.status.in_(
                [str(value) for value in ReceivableStatus if value.is_collectible]
            )
        )
        if provider is not None:
            stmt = stmt.where(CodReceivable.provider == provider)

        rows = list((await self._db.execute(stmt)).scalars().all())
        totals: dict[str, tuple[int, int]] = {bucket.label: (0, 0) for bucket in AGING_BUCKETS}

        for receivable in rows:
            outstanding = receivable.outstanding_paisa
            if outstanding <= 0:
                continue
            days = receivable.age_in_days(as_of=moment)
            if days is None:
                continue
            for bucket in AGING_BUCKETS:
                if bucket.contains(days):
                    count, amount = totals[bucket.label]
                    totals[bucket.label] = (count + 1, amount + outstanding)
                    break

        return [
            (bucket, totals[bucket.label][0], totals[bucket.label][1]) for bucket in AGING_BUCKETS
        ]

    async def outstanding_total(self, *, provider: str | None = None) -> int:
        aging = await self.aging(provider=provider)
        return sum(amount for _, _, amount in aging)

    # ------------------------------------------------------------ internals --

    async def _get(self, receivable_id: uuid.UUID) -> CodReceivable:
        receivable = await self._db.get(CodReceivable, receivable_id)
        if receivable is None:
            raise NotFoundError("Receivable not found")
        return receivable

    async def _require(self, consignment_id: uuid.UUID) -> CodReceivable:
        receivable = await self.for_consignment(consignment_id)
        if receivable is None:
            raise NotFoundError("That parcel has no receivable")
        return receivable

    def _transition(self, receivable: CodReceivable, target: ReceivableStatus) -> None:
        current = receivable.receivable_status
        if not can_transition(current, target):
            raise ConflictError(
                f"A receivable cannot go from {current} to {target}",
                details={"from": str(current), "to": str(target)},
            )
        receivable.status = str(target)
