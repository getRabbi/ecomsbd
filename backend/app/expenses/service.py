"""Recording expenses and pushing them down onto parcels.

Master spec section 86. Allocation is a deliberate, named act: it uses one of
three methods, records which one, and writes a new profit snapshot revision per
affected parcel so the old figures survive.

The section's closing warning is the one that shapes the API: *"do not rewrite
historical profit silently when seller changes future allocation preference."*
Changing :attr:`Expense.preferred_method` changes nothing that already
happened. Re-allocating an old period is a separate call that takes a reason.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import AuditAction, record_audit
from app.common.money import Money
from app.common.pagination import Cursor, apply_cursor
from app.consignments.models import Consignment, ConsignmentItem, ConsignmentStatus
from app.core.clock import utc_now
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.expenses.models import (
    ALLOCATION_VERSION,
    AllocationMethod,
    Expense,
    ExpenseAllocation,
    ExpenseKind,
)
from app.orders.models import OrderItem
from app.profit.models import ProfitSnapshot
from app.profit.service import ProfitService

__all__ = ["AllocationReport", "ExpenseService"]


@dataclass(slots=True)
class AllocationReport:
    """What an allocation run did."""

    expense_id: uuid.UUID
    method: AllocationMethod
    parcel_count: int
    allocated_paisa: int
    unallocated_paisa: int

    @property
    def reached_nothing(self) -> bool:
        return self.parcel_count == 0


class ExpenseService:
    """Records spend and apportions it."""

    def __init__(self, session: AsyncSession, *, profit: ProfitService | None = None) -> None:
        self._db = session
        self._profit = profit or ProfitService(session)

    # ------------------------------------------------------------ recording --

    async def record(
        self,
        *,
        kind: ExpenseKind,
        amount_paisa: int,
        period_start: date,
        period_end: date,
        description: str,
        product_id: uuid.UUID | None = None,
        preferred_method: AllocationMethod = AllocationMethod.EQUAL_PER_DELIVERED_ORDER,
    ) -> Expense:
        """Record money spent.

        Nothing happens to any profit figure here. An expense affects orders
        only when it is allocated, and that is a separate, named step.
        """
        if amount_paisa <= 0:
            raise ValidationError("An expense must be a positive amount")
        if period_end < period_start:
            raise ValidationError("The period ends before it starts")
        if preferred_method is AllocationMethod.PRODUCT_TAGGED_PER_UNIT and product_id is None:
            raise ValidationError("Per-product allocation needs the product the spend was for")

        expense = Expense(
            kind=str(kind),
            amount_paisa=amount_paisa,
            period_start=period_start,
            period_end=period_end,
            description=description,
            product_id=product_id,
            preferred_method=str(preferred_method),
        )
        self._db.add(expense)
        await self._db.flush()

        await record_audit(
            self._db,
            action=AuditAction.EXPENSE_RECORDED,
            entity_type="expense",
            entity_id=expense.id,
            context={
                "kind": str(kind),
                "amount_paisa": amount_paisa,
                "period": f"{period_start}..{period_end}",
            },
        )
        return expense

    async def get(self, expense_id: uuid.UUID) -> Expense:
        expense = await self._db.get(Expense, expense_id)
        if expense is None:
            raise NotFoundError("Expense not found")
        return expense

    async def list_expenses(
        self,
        *,
        limit: int = 30,
        cursor: Cursor | None = None,
        kind: ExpenseKind | None = None,
        since: date | None = None,
        until: date | None = None,
    ) -> list[Expense]:
        stmt = sa.select(Expense)
        if kind is not None:
            stmt = stmt.where(Expense.kind == str(kind))
        if since is not None:
            stmt = stmt.where(Expense.period_end >= since)
        if until is not None:
            stmt = stmt.where(Expense.period_start <= until)
        stmt = apply_cursor(stmt, Expense, cursor)
        stmt = stmt.order_by(Expense.created_at.desc(), Expense.id.desc()).limit(limit + 1)
        return list((await self._db.execute(stmt)).scalars().all())

    async def allocations_for(self, consignment_id: uuid.UUID) -> list[ExpenseAllocation]:
        result = await self._db.execute(
            sa.select(ExpenseAllocation).where(ExpenseAllocation.consignment_id == consignment_id)
        )
        return list(result.scalars().all())

    # ----------------------------------------------------------- allocating --

    async def allocate(
        self,
        expense_id: uuid.UUID,
        *,
        method: AllocationMethod | None = None,
        reason: str | None = None,
    ) -> AllocationReport:
        """Push an expense down onto the parcels it paid for.

        Re-allocating an already-allocated expense requires a reason: it
        rewrites profit figures the seller may have already read, and section
        86 forbids doing that silently.

        The split uses largest-remainder distribution, so the parts sum back to
        the exact expense. A rounding scheme that lost a paisa per parcel would
        make the Insights total disagree with the expense list, and a seller
        who noticed would be right not to trust either.
        """
        expense = await self.get(expense_id)

        if not expense.expense_kind.is_allocatable:
            # Section 18.1 keeps fixed costs below contribution profit, and
            # section 86 warns against pretending fixed-cost allocation is
            # accounting-grade. So this app does not spread rent across parcels.
            raise ConflictError(
                "Only ad spend is allocated to parcels",
                details={"kind": expense.kind},
            )

        if expense.is_allocated and not (reason or "").strip():
            raise ValidationError(
                "Re-allocating changes profit figures you have already seen. Say why."
            )

        chosen = method or expense.allocation_preference
        parcels = await self._parcels_for(expense, chosen)

        if not parcels:
            # Ad money spent in a period with no deliveries is a real cost that
            # no order carries. Recorded as unallocated rather than smeared
            # onto unrelated parcels, which would flatter their margins.
            expense.allocated_at = utc_now()
            expense.allocated_paisa = 0
            await self._db.flush()
            return AllocationReport(
                expense_id=expense.id,
                method=chosen,
                parcel_count=0,
                allocated_paisa=0,
                unallocated_paisa=expense.amount_paisa,
            )

        weights = await self._weights_for(expense, parcels, chosen)
        shares = Money(expense.amount_paisa).allocate(weights)

        now = utc_now()
        await self._clear_previous(expense.id)

        allocated = 0
        for consignment, share in zip(parcels, shares, strict=True):
            self._db.add(
                ExpenseAllocation(
                    expense_id=expense.id,
                    consignment_id=consignment.id,
                    amount_paisa=share.paisa,
                    method=str(chosen),
                    version=ALLOCATION_VERSION,
                    allocated_at=now,
                )
            )
            allocated += share.paisa
        await self._db.flush()

        # A new snapshot revision per parcel, carrying the method that produced
        # the figure. The previous revisions stay readable (section 17.4).
        for consignment in parcels:
            total = await self._ad_cost_for(consignment.id)
            await self._profit.snapshot(
                consignment.id,
                ad_cost_paisa=total,
                allocation_method=str(chosen),
                allocation_version=ALLOCATION_VERSION,
                reason=reason or f"Allocated {expense.description}",
            )

        expense.allocated_at = now
        expense.allocated_paisa = allocated
        await self._db.flush()

        await record_audit(
            self._db,
            action=AuditAction.EXPENSE_ALLOCATED,
            entity_type="expense",
            entity_id=expense.id,
            reason=reason,
            context={
                "method": str(chosen),
                "parcel_count": len(parcels),
                "allocated_paisa": allocated,
            },
        )
        return AllocationReport(
            expense_id=expense.id,
            method=chosen,
            parcel_count=len(parcels),
            allocated_paisa=allocated,
            unallocated_paisa=expense.amount_paisa - allocated,
        )

    # ------------------------------------------------------------ internals --

    async def _parcels_for(self, expense: Expense, method: AllocationMethod) -> list[Consignment]:
        """Delivered parcels the spend could plausibly have bought.

        Delivered only. A returned parcel earned nothing, and loading it with
        ad cost would turn one loss into a larger one for no reason a seller
        could act on.
        """
        stmt = (
            sa.select(Consignment)
            .where(
                Consignment.status.in_(
                    [
                        str(ConsignmentStatus.DELIVERED),
                        str(ConsignmentStatus.PARTIAL_DELIVERED),
                    ]
                ),
                Consignment.delivered_at.is_not(None),
            )
            .order_by(Consignment.delivered_at.asc(), Consignment.id.asc())
        )
        rows = list((await self._db.execute(stmt)).scalars().all())

        in_period = [
            consignment
            for consignment in rows
            if consignment.delivered_at is not None
            and expense.period_start <= consignment.delivered_at.date() <= expense.period_end
        ]

        if method is not AllocationMethod.PRODUCT_TAGGED_PER_UNIT:
            return in_period

        # Product-tagged spend reaches only the parcels that carried it.
        if expense.product_id is None:
            return []
        matching = await self._consignments_with_product(
            [consignment.id for consignment in in_period], expense.product_id
        )
        return [consignment for consignment in in_period if consignment.id in matching]

    async def _consignments_with_product(
        self, consignment_ids: list[uuid.UUID], product_id: uuid.UUID
    ) -> set[uuid.UUID]:
        if not consignment_ids:
            return set()
        rows = await self._db.execute(
            sa.select(ConsignmentItem.consignment_id)
            .join(OrderItem, OrderItem.id == ConsignmentItem.order_item_id)
            .where(
                ConsignmentItem.consignment_id.in_(consignment_ids),
                OrderItem.product_id == product_id,
                ConsignmentItem.qty_delivered > 0,
            )
        )
        return {row[0] for row in rows.all()}

    async def _weights_for(
        self,
        expense: Expense,
        parcels: list[Consignment],
        method: AllocationMethod,
    ) -> list[int]:
        """The share each parcel carries, before the money is split."""
        if method is AllocationMethod.EQUAL_PER_DELIVERED_ORDER:
            return [1] * len(parcels)

        if method is AllocationMethod.PROPORTIONAL_TO_REVENUE:
            return [max(0, consignment.collectible_paisa) for consignment in parcels]

        # PRODUCT_TAGGED_PER_UNIT: per delivered unit of the tagged product, so
        # a parcel with three of them carries three shares.
        counts = await self._delivered_units(
            [consignment.id for consignment in parcels], expense.product_id
        )
        return [counts.get(consignment.id, 0) for consignment in parcels]

    async def _delivered_units(
        self, consignment_ids: list[uuid.UUID], product_id: uuid.UUID | None
    ) -> dict[uuid.UUID, int]:
        if not consignment_ids or product_id is None:
            return {}
        rows = await self._db.execute(
            sa.select(
                ConsignmentItem.consignment_id,
                sa.func.coalesce(sa.func.sum(ConsignmentItem.qty_delivered), 0),
            )
            .join(OrderItem, OrderItem.id == ConsignmentItem.order_item_id)
            .where(
                ConsignmentItem.consignment_id.in_(consignment_ids),
                OrderItem.product_id == product_id,
            )
            .group_by(ConsignmentItem.consignment_id)
        )
        return {row[0]: int(row[1]) for row in rows.all()}

    async def _clear_previous(self, expense_id: uuid.UUID) -> None:
        """Drop this expense's earlier shares before writing new ones.

        Only this expense's rows. Other expenses' allocations to the same
        parcels stay, which is why ``_ad_cost_for`` re-sums rather than
        assuming one source.
        """
        await self._db.execute(
            sa.delete(ExpenseAllocation).where(ExpenseAllocation.expense_id == expense_id)
        )
        await self._db.flush()

    async def _ad_cost_for(self, consignment_id: uuid.UUID) -> int:
        """Every expense's share of one parcel, summed."""
        result = await self._db.execute(
            sa.select(sa.func.coalesce(sa.func.sum(ExpenseAllocation.amount_paisa), 0)).where(
                ExpenseAllocation.consignment_id == consignment_id
            )
        )
        return int(result.scalar_one() or 0)

    # ------------------------------------------------------------ reporting --

    async def totals(
        self, *, since: date | None = None, until: date | None = None
    ) -> dict[str, int]:
        """Spend over a period, split by whether it reached any parcel."""
        stmt = sa.select(
            Expense.kind,
            sa.func.coalesce(sa.func.sum(Expense.amount_paisa), 0),
            sa.func.coalesce(sa.func.sum(Expense.allocated_paisa), 0),
        ).group_by(Expense.kind)
        if since is not None:
            stmt = stmt.where(Expense.period_end >= since)
        if until is not None:
            stmt = stmt.where(Expense.period_start <= until)

        totals = {
            "ad_spend_paisa": 0,
            "fixed_paisa": 0,
            "other_paisa": 0,
            "allocated_paisa": 0,
            "unallocated_paisa": 0,
        }
        for kind, amount, allocated in (await self._db.execute(stmt)).all():
            bucket = {
                str(ExpenseKind.AD_SPEND): "ad_spend_paisa",
                str(ExpenseKind.FIXED): "fixed_paisa",
                str(ExpenseKind.OTHER): "other_paisa",
            }[kind]
            totals[bucket] += int(amount or 0)
            totals["allocated_paisa"] += int(allocated or 0)
            totals["unallocated_paisa"] += int(amount or 0) - int(allocated or 0)
        return totals

    async def snapshot_ad_cost(self, consignment_id: uuid.UUID) -> int:
        """What the current snapshot says this parcel carries.

        Used by tests and the API to check the allocation and the snapshot
        agree — they are written in the same transaction, and a drift between
        them would mean the Insights total no longer matches the expense list.
        """
        result = await self._db.execute(
            sa.select(ProfitSnapshot.ad_cost_paisa).where(
                ProfitSnapshot.consignment_id == consignment_id,
                ProfitSnapshot.is_current.is_(True),
            )
        )
        return int(result.scalar_one_or_none() or 0)
