"""Dispatching parcels and recording what happened to them.

Phase D runs this in **manual courier mode**: the seller records that a parcel
went out and, later, that it was delivered or came back. No provider is
contacted — the Steadfast adapter is Phase C and is blocked on documentation
(master spec section 140 forbids inventing endpoints), and manual mode is a
first-class path in the product regardless (module 2: "Steadfast + manual").

The reason this module lives in Phase D at all is that a COD receivable needs a
delivery event to exist. Without one, the money core would have nothing to
reconcile and would only be testable against fixtures.

Two rules shape it:

* **Delivery is an outcome, not a status flag.** Recording a delivery moves
  stock, creates a receivable and writes ledger entries in the same
  transaction. A status that changed without those would be a lie the Money
  screen then repeats.
* **A partial delivery collects only what was delivered.** Section 17.8 forbids
  assuming the original COD, so the collectible amount is computed from the
  per-item quantities.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.common.audit import AuditAction, record_audit
from app.consignments.models import (
    MANUAL_PROVIDER,
    Consignment,
    ConsignmentItem,
    ConsignmentStatus,
)
from app.core.clock import utc_now
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.money.service import ReceivableService
from app.orders.models import Order, OrderItem, OrderStatus
from app.products.models import StockMovementReason, StockMovementSource
from app.products.service import StockAdjustment, StockService
from app.profit.models import ReturnReason
from app.profit.service import ProfitService

__all__ = ["ConsignmentService", "DeliveryOutcome", "ItemOutcome"]


@dataclass(frozen=True, slots=True)
class ItemOutcome:
    """What happened to the units of one order line."""

    consignment_item_id: uuid.UUID
    qty_delivered: int
    qty_returned: int


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    """The seller's report of how a parcel ended.

    ``items`` is optional for a clean full delivery or a clean full return,
    where every unit shared the same fate. It is required for a partial, since
    that is the only way to know what was actually collected.
    """

    status: ConsignmentStatus
    occurred_at: datetime | None = None
    items: list[ItemOutcome] = field(default_factory=list)
    note: str | None = None
    #: Why it came back (master spec section 19). Recorded on the profit
    #: snapshot, which is what makes the return report able to say *why* a
    #: product's returns cost the seller money rather than only that they did.
    return_reason: ReturnReason | None = None


class ConsignmentService:
    """Creates parcels and records their outcomes."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        receivables: ReceivableService,
        stock: StockService | None = None,
        profit: ProfitService | None = None,
    ) -> None:
        self._db = session
        self._receivables = receivables
        self._profit = profit or ProfitService(session)
        self._stock = stock or StockService(session)

    async def get(self, consignment_id: uuid.UUID) -> Consignment:
        result = await self._db.execute(
            sa.select(Consignment)
            .where(Consignment.id == consignment_id)
            .options(selectinload(Consignment.items))
        )
        consignment = result.scalar_one_or_none()
        if consignment is None:
            raise NotFoundError("Consignment not found")
        return consignment

    # ------------------------------------------------------------- dispatch --

    async def dispatch_manual(
        self,
        order_id: uuid.UUID,
        *,
        provider: str = MANUAL_PROVIDER,
        tracking_code: str | None = None,
        cod_amount_paisa: int | None = None,
        occurred_at: datetime | None = None,
    ) -> Consignment:
        """Record that a parcel has gone out with a courier.

        This decrements stock (section 20's default decrement moment) and puts
        the receivable in ``EXPECTED`` — expected, not collectible, because
        nobody owes anything until the parcel is delivered.

        No provider is contacted. In manual mode there is nobody to contact; in
        Phase C this becomes the post-booking half of the flow, after the
        adapter has returned.
        """
        order = await self._db.get(Order, order_id)
        if order is None:
            raise NotFoundError("Order not found")

        if order.order_status in (OrderStatus.CANCELLED, OrderStatus.COMPLETED):
            raise ConflictError(
                "That order is finished and cannot be dispatched",
                details={"status": order.status},
            )

        existing = await self._db.execute(
            sa.select(Consignment).where(
                Consignment.order_id == order_id,
                Consignment.status.notin_(
                    [
                        str(ConsignmentStatus.CANCELLED),
                        str(ConsignmentStatus.FAILED),
                    ]
                ),
            )
        )
        if existing.scalars().first() is not None:
            # Two live parcels for one order means two couriers collecting the
            # same COD, and a receivable that can never balance.
            raise ConflictError(
                "That order already has a parcel out",
                details={"order_id": str(order_id)},
            )

        moment = occurred_at or utc_now()
        # Validated before the consignment row exists: an order with no items
        # cannot be dispatched, and finding that out after creating the parcel
        # would leave a consignment with nothing in it.
        await self._load_order_items(order_id)

        consignment = Consignment(
            order_id=order_id,
            provider=provider,
            tracking_code=tracking_code,
            # The reference we can find this parcel by in a statement, and the
            # key that resolves a BOOKING_UNKNOWN in Phase C.
            merchant_reference=order.order_number,
            status=str(ConsignmentStatus.BOOKED),
            cod_amount_paisa=(
                cod_amount_paisa if cod_amount_paisa is not None else order.cod_amount_paisa
            ),
            booked_at=moment,
            last_status_at=moment,
        )
        self._db.add(consignment)
        await self._db.flush()

        await self.fulfil_dispatch(consignment, order=order, occurred_at=moment)
        return consignment

    async def fulfil_dispatch(
        self,
        consignment: Consignment,
        *,
        order: Order,
        occurred_at: datetime | None = None,
    ) -> Consignment:
        """Everything that follows a parcel physically going out.

        Lines, stock decrement, receivable, audit — one path, whether the
        parcel was recorded by hand or booked through a provider API. Split out
        of :meth:`dispatch_manual` when courier booking arrived, because a
        provider booking has to do exactly this and **only after** the provider
        confirms: a parcel whose create call timed out has not left the shelf,
        and decrementing stock for it would make the shelf disagree with
        reality in the one case where a person has to go and count.

        Idempotent on the items: called twice for the same consignment — a
        recovery that runs alongside a late provider answer — the second call
        finds lines already there and does nothing rather than decrementing
        stock twice.
        """
        moment = occurred_at or utc_now()

        existing = await self._db.execute(
            sa.select(sa.func.count())
            .select_from(ConsignmentItem)
            .where(ConsignmentItem.consignment_id == consignment.id)
        )
        if existing.scalar_one() > 0:
            # Already fulfilled. The receivable call below is itself idempotent,
            # so re-running it is safe and keeps a partially-applied dispatch
            # from staying that way.
            await self._receivables.open_for(consignment, order=order)
            return consignment

        items = await self._load_order_items(consignment.order_id)
        for order_item, unit_collectible in items:
            self._db.add(
                ConsignmentItem(
                    consignment_id=consignment.id,
                    order_item_id=order_item.id,
                    qty_shipped=order_item.quantity,
                    unit_collectible_paisa=unit_collectible,
                    unit_cost_snapshot_paisa=order_item.unit_cost_snapshot_paisa,
                )
            )
            if order_item.product_id is not None:
                await self._stock.record_movement(
                    StockAdjustment(
                        product_id=order_item.product_id,
                        quantity_delta=-order_item.quantity,
                        reason=StockMovementReason.BOOKED_DECREMENT,
                        source=StockMovementSource.SYSTEM,
                        order_id=consignment.order_id,
                        order_item_id=order_item.id,
                        consignment_id=consignment.id,
                        occurred_at=moment,
                    ),
                    # Stock going negative here is a real state — the parcel is
                    # physically gone — and refusing it would leave the ledger
                    # disagreeing with the shelf.
                    allow_negative=True,
                )

        await self._db.flush()
        await self._db.refresh(consignment, attribute_names=["items"])

        await self._receivables.open_for(consignment, order=order)

        await record_audit(
            self._db,
            action=AuditAction.CONSIGNMENT_DISPATCHED,
            entity_type="consignment",
            entity_id=consignment.id,
            context={
                "order_number": order.order_number,
                "provider": consignment.provider,
                "cod_amount_paisa": consignment.cod_amount_paisa,
            },
        )
        return consignment

    # ------------------------------------------------------------- outcomes --

    async def record_outcome(
        self, consignment_id: uuid.UUID, outcome: DeliveryOutcome
    ) -> Consignment:
        """Record how a parcel ended, and move the money that follows.

        Everything downstream of the outcome — collectible amount, receivable
        state, stock restoration, ledger entries — happens here, in one
        transaction. Splitting them would let a delivery exist without its
        receivable, which is exactly the "delivered but unpaid" gap the product
        is meant to close.
        """
        consignment = await self.get(consignment_id)
        current = consignment.consignment_status

        if current.is_terminal:
            raise ConflictError(
                "That parcel already has a final outcome",
                details={"status": consignment.status},
            )

        moment = outcome.occurred_at or utc_now()
        target = outcome.status

        if target is ConsignmentStatus.PARTIAL_DELIVERED and not outcome.items:
            # Without per-item quantities there is no honest way to say what was
            # collected, and guessing the whole COD is the section 17.8 error.
            raise ValidationError("A partial delivery needs the delivered and returned quantities")

        if outcome.items:
            self._apply_item_outcomes(consignment, outcome.items)
        elif target is ConsignmentStatus.DELIVERED:
            for item in consignment.items:
                item.qty_delivered = item.qty_shipped
                item.qty_returned = 0
        elif target in (ConsignmentStatus.RETURNED, ConsignmentStatus.CANCELLED):
            for item in consignment.items:
                item.qty_delivered = 0
                item.qty_returned = item.qty_shipped

        consignment.status = str(target)
        consignment.last_status_at = moment
        if target in (ConsignmentStatus.DELIVERED, ConsignmentStatus.PARTIAL_DELIVERED):
            consignment.delivered_at = moment
        if target in (ConsignmentStatus.RETURNED, ConsignmentStatus.CANCELLED):
            consignment.returned_at = moment
        await self._db.flush()

        await self._restore_returned_stock(consignment, moment)

        if target.produces_collectible:
            await self._receivables.mark_eligible(
                consignment, collected_at=moment, note=outcome.note
            )
        else:
            await self._receivables.mark_not_due(
                consignment,
                reason=outcome.note or f"Parcel {target}",
                occurred_at=moment,
            )

        if target.is_terminal:
            # The parcel is finished, so its profit is knowable — freeze it
            # now rather than waiting for someone to ask. A snapshot written
            # only when the Insights screen is opened would mean the figure
            # depended on when the seller looked at it, and a parcel nobody
            # looked at would never appear in a total at all.
            await self._profit.snapshot(consignment.id, return_reason=outcome.return_reason)

        await record_audit(
            self._db,
            action=AuditAction.CONSIGNMENT_STATUS_CHANGED,
            entity_type="consignment",
            entity_id=consignment.id,
            context={
                "from": str(current),
                "to": str(target),
                "collectible_paisa": consignment.collectible_paisa,
            },
        )
        return consignment

    # -------------------------------------------------------------- helpers --

    async def _load_order_items(self, order_id: uuid.UUID) -> list[tuple[OrderItem, int]]:
        """Order lines with the per-unit amount each contributes to the COD.

        The collectible amount has to add up to the order's COD, which is not
        simply the sum of the line totals once a discount or a delivery fee is
        involved. Rather than re-deriving it, each unit carries its line's share
        and the remainder is left on the consignment's own COD figure — the
        parcel total stays authoritative, and a partial delivery is measured
        against the units actually handed over.
        """
        result = await self._db.execute(
            sa.select(OrderItem)
            .where(OrderItem.order_id == order_id)
            .order_by(OrderItem.created_at.asc())
        )
        items = list(result.scalars().all())
        if not items:
            raise ValidationError("An order with no items cannot be dispatched")

        return [
            (
                item,
                item.line_total_paisa // item.quantity if item.quantity else 0,
            )
            for item in items
        ]

    def _apply_item_outcomes(self, consignment: Consignment, outcomes: list[ItemOutcome]) -> None:
        by_id = {item.id: item for item in consignment.items}
        for outcome in outcomes:
            item = by_id.get(outcome.consignment_item_id)
            if item is None:
                raise ValidationError(
                    "That line is not part of this parcel",
                    details={"consignment_item_id": str(outcome.consignment_item_id)},
                )
            if outcome.qty_delivered < 0 or outcome.qty_returned < 0:
                raise ValidationError("Quantities cannot be negative")
            if outcome.qty_delivered + outcome.qty_returned > item.qty_shipped:
                # The database enforces this too; failing here gives the seller
                # a message naming the line rather than a constraint violation.
                raise ValidationError(
                    "More units accounted for than were shipped",
                    details={
                        "order_item_id": str(item.order_item_id),
                        "shipped": item.qty_shipped,
                        "delivered": outcome.qty_delivered,
                        "returned": outcome.qty_returned,
                    },
                )
            item.qty_delivered = outcome.qty_delivered
            item.qty_returned = outcome.qty_returned

    async def _restore_returned_stock(self, consignment: Consignment, moment: datetime) -> None:
        """Put returned units back on the shelf.

        Section 16 lists "returned but inventory not restored" as one of the
        reconciliation cases; doing it here as part of the outcome is how that
        case stays empty.

        The product id comes from the order line rather than from the
        consignment item, which deliberately stores only quantities and money —
        one place owns what a line *is*, and it is the order.
        """
        returned = [item for item in consignment.items if item.qty_returned > 0]
        if not returned:
            return

        is_partial = consignment.consignment_status is ConsignmentStatus.PARTIAL_DELIVERED
        reason = (
            StockMovementReason.PARTIAL_RETURN_RESTORE
            if is_partial
            else StockMovementReason.RETURN_RESTORE
        )
        if consignment.consignment_status is ConsignmentStatus.CANCELLED:
            reason = StockMovementReason.CANCEL_RESTORE

        rows = await self._db.execute(
            sa.select(OrderItem).where(OrderItem.id.in_([item.order_item_id for item in returned]))
        )
        products = {row.id: row.product_id for row in rows.scalars().all()}

        for item in returned:
            product_id = products.get(item.order_item_id)
            if product_id is None:
                # A free-text line with no catalogue product behind it. There
                # is no stock to restore, and inventing one would be worse.
                continue
            await self._stock.record_movement(
                StockAdjustment(
                    product_id=product_id,
                    quantity_delta=item.qty_returned,
                    reason=reason,
                    source=StockMovementSource.SYSTEM,
                    order_id=consignment.order_id,
                    order_item_id=item.order_item_id,
                    consignment_id=consignment.id,
                    occurred_at=moment,
                )
            )
