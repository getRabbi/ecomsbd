"""Recording charges and producing profit snapshots.

The write paths for the two immutable tables. Neither ever updates a figure in
place:

* :meth:`ProfitService.record_charge` appends, and supersedes an earlier figure
  of the same kind rather than overwriting it (section 17.3);
* :meth:`ProfitService.snapshot` writes a new revision and marks the previous
  one superseded (section 17.4).

Section 55's acceptance criterion — *"old order profit does not change after
new rate rule"* — is what both of those exist for.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.common.audit import AuditAction, record_audit
from app.common.pagination import Cursor, apply_cursor
from app.consignments.models import Consignment, ConsignmentStatus
from app.core.clock import business_date, utc_now
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.money.models import CodReceivable, ReceivableStatus
from app.orders.models import Order
from app.profit.engine import (
    ChargeInput,
    ProfitInput,
    ProfitResult,
    calculate_profit,
)
from app.profit.models import (
    CALCULATION_VERSION,
    ChargeKind,
    ChargeSource,
    ConsignmentCharge,
    ProfitQuality,
    ProfitSnapshot,
    ReturnReason,
)

__all__ = ["ProfitService"]


class ProfitService:
    """The only writer of charges and profit snapshots."""

    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    # -------------------------------------------------------------- charges --

    async def record_charge(
        self,
        consignment_id: uuid.UUID,
        *,
        kind: ChargeKind,
        amount_paisa: int,
        source: ChargeSource,
        provider_label: str | None = None,
        source_ref: str | None = None,
        reason: str | None = None,
        occurred_at: datetime | None = None,
    ) -> ConsignmentCharge:
        """Record what a parcel cost.

        If a charge of this kind already exists, the new one supersedes it —
        which is the normal path when a booking estimate is replaced by the
        settled figure off a statement. The old row is left exactly as written
        apart from its ``superseded_by`` pointer, so both numbers survive and a
        seller can see that the courier charged more than it quoted.

        A charge is only replaced by one at least as trustworthy. A configured
        estimate arriving after a settled figure is ignored rather than allowed
        to overwrite the truth (section 85's hierarchy).
        """
        if amount_paisa < 0:
            raise ValidationError("A charge cannot be negative")

        current = await self._current_charge(consignment_id, kind)
        if current is not None and source.rank > current.charge_source.rank and reason is None:
            # A weaker source cannot silently replace a stronger one. A person
            # may still do it by supplying a reason, which is section 17.3's
            # "audited correction".
            raise ConflictError(
                "That charge is already known from a better source",
                details={
                    "existing_source": current.source,
                    "offered_source": str(source),
                },
            )

        moment = occurred_at or utc_now()
        charge = ConsignmentCharge(
            consignment_id=consignment_id,
            kind=str(kind),
            source=str(source),
            amount_paisa=amount_paisa,
            provider_label=provider_label,
            source_ref=source_ref,
            reason=reason,
            occurred_at=moment,
        )
        self._db.add(charge)
        await self._db.flush()

        if current is not None:
            current.superseded_by = charge.id
            await self._db.flush()
            await record_audit(
                self._db,
                action=AuditAction.CHARGE_SUPERSEDED,
                entity_type="consignment_charge",
                entity_id=charge.id,
                reason=reason,
                context={
                    "kind": str(kind),
                    "from_paisa": current.amount_paisa,
                    "to_paisa": amount_paisa,
                    "from_source": current.source,
                    "to_source": str(source),
                },
            )
        return charge

    async def charges_for(
        self, consignment_id: uuid.UUID, *, include_superseded: bool = False
    ) -> list[ConsignmentCharge]:
        stmt = sa.select(ConsignmentCharge).where(
            ConsignmentCharge.consignment_id == consignment_id
        )
        if not include_superseded:
            stmt = stmt.where(ConsignmentCharge.superseded_by.is_(None))
        stmt = stmt.order_by(ConsignmentCharge.occurred_at.asc())
        return list((await self._db.execute(stmt)).scalars().all())

    async def _current_charge(
        self, consignment_id: uuid.UUID, kind: ChargeKind
    ) -> ConsignmentCharge | None:
        result = await self._db.execute(
            sa.select(ConsignmentCharge).where(
                ConsignmentCharge.consignment_id == consignment_id,
                ConsignmentCharge.kind == str(kind),
                ConsignmentCharge.superseded_by.is_(None),
            )
        )
        return result.scalars().first()

    # ------------------------------------------------------------ snapshots --

    async def snapshot(
        self,
        consignment_id: uuid.UUID,
        *,
        return_reason: ReturnReason | None = None,
        ad_cost_paisa: int = 0,
        allocation_method: str | None = None,
        allocation_version: int | None = None,
        reason: str | None = None,
    ) -> ProfitSnapshot:
        """Freeze what this parcel earned.

        Called when a parcel reaches a terminal outcome, and again whenever
        something behind the figure changes — a settled charge replacing an
        estimate, or an expense being allocated. Each call writes a new revision
        rather than editing the last, so a seller who read a number in March can
        still see that number in December alongside whatever replaced it.
        """
        consignment = await self._consignment(consignment_id)
        if not consignment.consignment_status.is_terminal:
            # A parcel still moving has no profit yet, and inventing one would
            # put a figure on the Insights screen that changes under the seller.
            raise ConflictError(
                "That parcel has not finished yet",
                details={"status": consignment.status},
            )

        result = await self._calculate(
            consignment,
            ad_cost_paisa=ad_cost_paisa,
            allocation_method=allocation_method,
            allocation_version=allocation_version,
        )

        previous = await self.current_snapshot(consignment_id)
        revision = (previous.revision + 1) if previous else 1
        settled_on = consignment.delivered_at or consignment.returned_at or utc_now()

        snapshot = ProfitSnapshot(
            consignment_id=consignment.id,
            order_id=consignment.order_id,
            business_date=business_date(at=settled_on),
            revision=revision,
            is_current=True,
            calculation_version=CALCULATION_VERSION,
            realized_revenue_paisa=result.realized_revenue_paisa,
            item_cost_paisa=result.item_cost_paisa,
            delivery_charge_paisa=result.delivery_charge_paisa,
            cod_fee_paisa=result.cod_fee_paisa,
            return_charge_paisa=result.return_charge_paisa,
            packaging_paisa=result.packaging_paisa,
            payment_fee_paisa=result.payment_fee_paisa,
            other_cost_paisa=result.other_cost_paisa,
            ad_cost_paisa=result.ad_cost_paisa,
            allocation_method=allocation_method,
            allocation_version=allocation_version,
            discount_paisa=result.discount_paisa,
            write_off_cost_paisa=result.write_off_cost_paisa,
            contribution_profit_paisa=result.contribution_profit_paisa,
            margin_basis_points=result.margin_basis_points,
            quality=str(result.quality),
            missing_inputs=result.missing_inputs,
            reason=reason,
            outcome=consignment.status,
            return_reason=str(return_reason) if return_reason else None,
            calculated_at=utc_now(),
        )
        self._db.add(snapshot)
        await self._db.flush()

        if previous is not None:
            # The previous revision is kept and pointed at the new one. Section
            # 17.4: a snapshot change is a new revision plus an audit event.
            previous.is_current = False
            previous.superseded_by = snapshot.id
            await self._db.flush()
            await record_audit(
                self._db,
                action=AuditAction.PROFIT_SNAPSHOT_REVISED,
                entity_type="profit_snapshot",
                entity_id=snapshot.id,
                reason=reason,
                context={
                    "revision": revision,
                    "from_paisa": previous.contribution_profit_paisa,
                    "to_paisa": snapshot.contribution_profit_paisa,
                },
            )
        return snapshot

    async def current_snapshot(self, consignment_id: uuid.UUID) -> ProfitSnapshot | None:
        result = await self._db.execute(
            sa.select(ProfitSnapshot).where(
                ProfitSnapshot.consignment_id == consignment_id,
                ProfitSnapshot.is_current.is_(True),
            )
        )
        return result.scalars().first()

    async def revisions(self, consignment_id: uuid.UUID) -> list[ProfitSnapshot]:
        """Every version of this parcel's profit, oldest first."""
        result = await self._db.execute(
            sa.select(ProfitSnapshot)
            .where(ProfitSnapshot.consignment_id == consignment_id)
            .order_by(ProfitSnapshot.revision.asc())
        )
        return list(result.scalars().all())

    async def list_snapshots(
        self,
        *,
        limit: int = 30,
        cursor: Cursor | None = None,
        since: date | None = None,
        until: date | None = None,
        losses_only: bool = False,
    ) -> list[ProfitSnapshot]:
        stmt = sa.select(ProfitSnapshot).where(ProfitSnapshot.is_current.is_(True))
        if since is not None:
            stmt = stmt.where(ProfitSnapshot.business_date >= since)
        if until is not None:
            stmt = stmt.where(ProfitSnapshot.business_date <= until)
        if losses_only:
            stmt = stmt.where(ProfitSnapshot.contribution_profit_paisa < 0)
        stmt = apply_cursor(stmt, ProfitSnapshot, cursor)
        stmt = stmt.order_by(ProfitSnapshot.created_at.desc(), ProfitSnapshot.id.desc()).limit(
            limit + 1
        )
        return list((await self._db.execute(stmt)).scalars().all())

    # ----------------------------------------------------------- internals --

    async def _consignment(self, consignment_id: uuid.UUID) -> Consignment:
        result = await self._db.execute(
            sa.select(Consignment)
            .where(Consignment.id == consignment_id)
            .options(selectinload(Consignment.items))
        )
        consignment = result.scalar_one_or_none()
        if consignment is None:
            raise NotFoundError("Consignment not found")
        return consignment

    async def _calculate(
        self,
        consignment: Consignment,
        *,
        ad_cost_paisa: int,
        allocation_method: str | None,
        allocation_version: int | None,
    ) -> ProfitResult:
        """Assemble the formula's inputs from what the shop actually knows."""
        charges = await self.charges_for(consignment.id)
        receivable = await self._receivable(consignment.id)

        revenue, revenue_source = self._revenue_for(consignment, receivable)
        item_cost = sum(item.delivered_cost_paisa for item in consignment.items)

        # Units that came back are only a cost if they cannot be sold again.
        # A sellable return is stock, not a loss — recording it as one would
        # make every return look twice as expensive as it was.
        write_off_cost = 0
        if consignment.consignment_status in (
            ConsignmentStatus.DAMAGED,
            ConsignmentStatus.LOST,
        ):
            write_off_cost = sum(
                item.returned_cost_paisa or item.delivered_cost_paisa for item in consignment.items
            )
        else:
            # V2.2: returned units the seller received back damaged (and did not
            # restock) are a loss at their order-time cost snapshot. Units not
            # yet received are still assumed sellable, as before.
            write_off_cost = sum(item.not_restocked_cost_paisa for item in consignment.items)

        order = await self._db.get(Order, consignment.order_id)

        return calculate_profit(
            ProfitInput(
                realized_revenue_paisa=revenue,
                item_cost_paisa=item_cost,
                write_off_cost_paisa=write_off_cost,
                charges=[
                    ChargeInput(
                        kind=charge.charge_kind,
                        amount_paisa=charge.amount_paisa,
                        source=charge.charge_source,
                    )
                    for charge in charges
                ],
                ad_cost_paisa=ad_cost_paisa,
                ad_source=(ChargeSource.SELLER if ad_cost_paisa > 0 else ChargeSource.UNKNOWN),
                allocation_method=allocation_method,
                allocation_version=allocation_version,
                discount_paisa=order.discount_paisa if order else 0,
                revenue_source=revenue_source,
                item_cost_source=(ChargeSource.SETTLED if item_cost > 0 else ChargeSource.UNKNOWN)
                if consignment.consignment_status.produces_collectible
                else ChargeSource.SETTLED,
            )
        )

    def _revenue_for(
        self, consignment: Consignment, receivable: CodReceivable | None
    ) -> tuple[int, ChargeSource]:
        """What was actually collected, and how sure we are.

        A parcel that came back collected nothing — and that is a *certain*
        zero, not an unknown, so it does not drag the figure's quality down.
        """
        if not consignment.consignment_status.produces_collectible:
            return 0, ChargeSource.SETTLED

        if receivable is None:
            return consignment.collectible_paisa, ChargeSource.BOOKED

        if receivable.receivable_status is ReceivableStatus.SETTLED:
            # Money has actually arrived, so revenue is what arrived rather
            # than what was owed. They differ when the courier deducted.
            return receivable.settled_paisa, ChargeSource.SETTLED

        # Delivered but not paid: what the courier collected is known, but
        # whether it reaches the seller is not.
        return receivable.collectible_paisa, ChargeSource.BOOKED

    async def _receivable(self, consignment_id: uuid.UUID) -> CodReceivable | None:
        result = await self._db.execute(
            sa.select(CodReceivable).where(CodReceivable.consignment_id == consignment_id)
        )
        return result.scalars().first()

    # ------------------------------------------------------------ reporting --

    async def totals(
        self, *, since: date | None = None, until: date | None = None
    ) -> dict[str, int]:
        """Summed current snapshots over a period.

        Only ``is_current`` rows are counted, so a revised parcel contributes
        once. Summing every revision would double-count exactly the orders that
        were corrected.
        """
        stmt = sa.select(
            sa.func.count(),
            sa.func.coalesce(sa.func.sum(ProfitSnapshot.realized_revenue_paisa), 0),
            sa.func.coalesce(sa.func.sum(ProfitSnapshot.item_cost_paisa), 0),
            sa.func.coalesce(sa.func.sum(ProfitSnapshot.delivery_charge_paisa), 0),
            sa.func.coalesce(sa.func.sum(ProfitSnapshot.cod_fee_paisa), 0),
            sa.func.coalesce(sa.func.sum(ProfitSnapshot.return_charge_paisa), 0),
            sa.func.coalesce(sa.func.sum(ProfitSnapshot.packaging_paisa), 0),
            sa.func.coalesce(sa.func.sum(ProfitSnapshot.ad_cost_paisa), 0),
            sa.func.coalesce(sa.func.sum(ProfitSnapshot.write_off_cost_paisa), 0),
            sa.func.coalesce(sa.func.sum(ProfitSnapshot.contribution_profit_paisa), 0),
        ).where(ProfitSnapshot.is_current.is_(True))

        if since is not None:
            stmt = stmt.where(ProfitSnapshot.business_date >= since)
        if until is not None:
            stmt = stmt.where(ProfitSnapshot.business_date <= until)

        row = (await self._db.execute(stmt)).one()
        return {
            "parcel_count": int(row[0]),
            "realized_revenue_paisa": int(row[1]),
            "item_cost_paisa": int(row[2]),
            "delivery_charge_paisa": int(row[3]),
            "cod_fee_paisa": int(row[4]),
            "return_charge_paisa": int(row[5]),
            "packaging_paisa": int(row[6]),
            "ad_cost_paisa": int(row[7]),
            "write_off_cost_paisa": int(row[8]),
            "contribution_profit_paisa": int(row[9]),
        }

    async def quality_breakdown(
        self, *, since: date | None = None, until: date | None = None
    ) -> dict[ProfitQuality, int]:
        """How many parcels' figures are actual, estimated or incomplete.

        The Insights screen shows this next to the headline so a seller can see
        how much of their profit number is measured — section 135's point about
        false precision.
        """
        stmt = (
            sa.select(ProfitSnapshot.quality, sa.func.count())
            .where(ProfitSnapshot.is_current.is_(True))
            .group_by(ProfitSnapshot.quality)
        )
        if since is not None:
            stmt = stmt.where(ProfitSnapshot.business_date >= since)
        if until is not None:
            stmt = stmt.where(ProfitSnapshot.business_date <= until)

        counts = dict.fromkeys(ProfitQuality, 0)
        for quality, count in (await self._db.execute(stmt)).all():
            counts[ProfitQuality(quality)] = int(count)
        return counts
