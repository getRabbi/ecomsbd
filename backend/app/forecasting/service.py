"""Demand forecasts, reorder suggestions, forecast accuracy and the cash outlook (V3.6).

Everything here reads the ledgers and never writes to them. The only rows it
writes are its own daily snapshots (``demand_forecasts``) and, for an item that
has just become at risk, one outbox event that workflows can react to.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.outbox import OutboxEvent, OutboxTopic, enqueue
from app.core.clock import business_date, business_day_bounds, ensure_utc, utc_now
from app.core.context import require_tenant_id
from app.forecasting.engine import (
    DEFAULT_COVER_DAYS,
    DEFAULT_LEAD_TIME_DAYS,
    HISTORY_DAYS,
    HORIZON_DAYS,
    Forecast,
    History,
    LeadTimeSource,
    forecast,
    median_days,
    wape,
)
from app.forecasting.models import DemandForecast
from app.procurement.models import (
    OPEN_PO,
    PurchaseOrder,
    PurchaseOrderLine,
    Supplier,
    SupplierItem,
    item_key,
)
from app.products.models import Product, ProductVariant, StockMovement, StockMovementReason

_SALE = str(StockMovementReason.BOOKED_DECREMENT)
_UNSALE = str(StockMovementReason.CANCEL_RESTORE)
#: Received orders a supplier needs before its own lead time replaces the
#: seller's figure, and how many recent ones are read.
MIN_LEAD_SAMPLES = 3
LEAD_SAMPLES = 10
#: Scored forecasts needed before an accuracy figure is shown at all.
MIN_SCORED_ITEMS = 10
#: Snapshots older than this are pruned by the daily job.
KEEP_SNAPSHOT_DAYS = 120


@dataclass(slots=True)
class Item:
    product_id: uuid.UUID
    variant_id: uuid.UUID | None
    name: str
    variant_name: str | None
    sku: str | None
    on_hand: int
    #: The business day the item was created. Days before it were not "in
    #: stock with no sales": the item did not exist.
    since: date | None = None

    @property
    def key(self) -> str:
        return item_key(self.product_id, self.variant_id)


@dataclass(slots=True)
class LeadTime:
    days: int
    source: LeadTimeSource
    samples: int
    supplier_id: uuid.UUID | None
    supplier_name: str | None


@dataclass(slots=True)
class ItemForecast:
    item: Item
    lead: LeadTime
    result: Forecast
    history: History


def _day(value: datetime | None) -> date | None:
    return business_date(at=ensure_utc(value)) if value is not None else None


class ForecastService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ------------------------------------------------------------- items ---

    async def items(
        self, product_id: uuid.UUID | None = None, variant_id: uuid.UUID | None = None
    ) -> list[Item]:
        """Every live, stock-tracked simple product and active variant."""
        live: list[sa.ColumnElement[bool]] = [
            Product.archived_at.is_(None),
            Product.is_active.is_(True),
            Product.stock_tracking_enabled.is_(True),
        ]
        if product_id is not None:
            live.append(Product.id == product_id)
        found: list[Item] = []
        if variant_id is None:
            simple = await self.db.execute(
                sa.select(
                    Product.id, Product.name, Product.sku, Product.stock_on_hand, Product.created_at
                ).where(*live, Product.has_variants.is_(False))
            )
            found += [
                Item(pid, None, name, None, sku, int(qty), _day(created))
                for pid, name, sku, qty, created in simple
            ]
        query = (
            sa.select(
                Product.id,
                ProductVariant.id,
                Product.name,
                ProductVariant.name,
                ProductVariant.sku,
                ProductVariant.stock_on_hand,
                ProductVariant.created_at,
            )
            .join(Product, Product.id == ProductVariant.product_id)
            .where(*live, ProductVariant.is_active.is_(True))
        )
        if variant_id is not None:
            query = query.where(ProductVariant.id == variant_id)
        found += [
            Item(pid, vid, name, vname, sku, int(qty), _day(created))
            for pid, vid, name, vname, sku, qty, created in await self.db.execute(query)
        ]
        return found

    # ----------------------------------------------------------- history ---

    async def histories(
        self, items: list[Item], today: date, days: int = HISTORY_DAYS
    ) -> dict[str, History]:
        """Daily net units sold, and whether each day was in stock, per item.

        Completed days only: the window ends yesterday, because a day still in
        progress would read as a slow one. Read from the stock ledger in one
        pass. A day is in stock if the item existed, started it with stock, or
        sold on it. The balance at the start of the window is the first later
        movement's balance before it (today's included), or today's count when
        the item has not moved since.
        """
        first_day = today - timedelta(days=days)
        start, _ = business_day_bounds(first_day)
        _, end = business_day_bounds(today)
        rows = await self.db.execute(
            sa.select(
                StockMovement.product_id,
                StockMovement.variant_id,
                StockMovement.reason,
                StockMovement.quantity_delta,
                StockMovement.balance_after,
                StockMovement.occurred_at,
            )
            .where(
                StockMovement.occurred_at >= start,
                StockMovement.occurred_at < end,
                # One product asked for: read only its rows.
                *(
                    [StockMovement.product_id == items[0].product_id]
                    if len({item.product_id for item in items}) == 1
                    else []
                ),
            )
            .order_by(StockMovement.occurred_at, StockMovement.id)
        )
        moves: dict[str, list[tuple[int, str, int, int]]] = defaultdict(list)
        for product_id, variant_id, reason, delta, balance, at in rows:
            index = (business_date(at=ensure_utc(at)) - first_day).days
            if 0 <= index <= days:
                moves[item_key(product_id, variant_id)].append(
                    (index, str(reason), int(delta), int(balance))
                )

        out: dict[str, History] = {}
        for item in items:
            units = [0] * days
            opening = [0] * days
            events = moves.get(item.key, [])
            balance = events[0][3] - events[0][2] if events else item.on_hand
            cursor = 0
            for day in range(days):
                opening[day] = balance
                while cursor < len(events) and events[cursor][0] == day:
                    _, reason, delta, after = events[cursor]
                    if reason in (_SALE, _UNSALE):
                        units[day] -= delta
                    balance = after
                    cursor += 1
            daily = tuple(max(0, u) for u in units)
            born = (item.since - first_day).days if item.since else 0
            in_stock = tuple(d >= born and (opening[d] > 0 or daily[d] > 0) for d in range(days))
            out[item.key] = History(daily, in_stock)
        return out

    # --------------------------------------------------------- lead time ---

    async def lead_times(self, items: list[Item]) -> dict[str, LeadTime]:
        """Per item: its preferred supplier's observed, entered or default lead time."""
        links = (
            await self.db.execute(
                sa.select(
                    SupplierItem.item_key, Supplier.id, Supplier.name, Supplier.lead_time_days
                )
                .join(Supplier, Supplier.id == SupplierItem.supplier_id)
                .where(SupplierItem.is_preferred.is_(True), Supplier.is_active.is_(True))
            )
        ).all()
        by_item = {key: (sid, name, entered) for key, sid, name, entered in links}
        observed = await self._observed_lead_times({sid for sid, _, _ in by_item.values()})
        out: dict[str, LeadTime] = {}
        for item in items:
            link = by_item.get(item.key)
            if link is None:
                out[item.key] = LeadTime(
                    DEFAULT_LEAD_TIME_DAYS, LeadTimeSource.DEFAULT, 0, None, None
                )
                continue
            sid, name, entered = link
            samples = observed.get(sid, [])
            if len(samples) >= MIN_LEAD_SAMPLES:
                days, source = median_days(samples), LeadTimeSource.OBSERVED
            elif entered is not None:
                days, source = int(entered), LeadTimeSource.SUPPLIER
            else:
                days, source = DEFAULT_LEAD_TIME_DAYS, LeadTimeSource.DEFAULT
            out[item.key] = LeadTime(max(days, 1), source, len(samples), sid, name)
        return out

    async def _observed_lead_times(
        self, supplier_ids: set[uuid.UUID]
    ) -> dict[uuid.UUID, list[int]]:
        if not supplier_ids:
            return {}
        rows = await self.db.execute(
            sa.select(
                PurchaseOrder.supplier_id, PurchaseOrder.ordered_at, PurchaseOrder.first_received_at
            )
            .where(
                PurchaseOrder.supplier_id.in_(supplier_ids),
                PurchaseOrder.ordered_at.is_not(None),
                PurchaseOrder.first_received_at.is_not(None),
            )
            .order_by(PurchaseOrder.first_received_at.desc())
        )
        out: dict[uuid.UUID, list[int]] = defaultdict(list)
        for sid, ordered, received in rows:
            if len(out[sid]) < LEAD_SAMPLES:
                seconds = (ensure_utc(received) - ensure_utc(ordered)).total_seconds()
                out[sid].append(max(0, round(seconds / 86_400)))
        return out

    async def incoming(self) -> dict[str, int]:
        """Units still to arrive on open purchase orders, per item."""
        rows = await self.db.execute(
            sa.select(
                PurchaseOrderLine.item_key,
                PurchaseOrderLine.quantity_ordered,
                PurchaseOrderLine.quantity_received,
                PurchaseOrderLine.quantity_rejected,
            )
            .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.purchase_order_id)
            .where(PurchaseOrder.status.in_(OPEN_PO))
        )
        out: dict[str, int] = defaultdict(int)
        for key, ordered, received, rejected in rows:
            out[key] += max(0, int(ordered) - int(received) - int(rejected))
        return out

    # ---------------------------------------------------------- forecast ---

    async def forecasts(
        self,
        *,
        today: date | None = None,
        cover_days: int = DEFAULT_COVER_DAYS,
        product_id: uuid.UUID | None = None,
        variant_id: uuid.UUID | None = None,
    ) -> list[ItemForecast]:
        today = today or business_date()
        items = await self.items(product_id, variant_id)
        if not items:
            return []
        histories = await self.histories(items, today)
        leads = await self.lead_times(items)
        incoming = await self.incoming()
        return [
            ItemForecast(
                item=item,
                lead=leads[item.key],
                history=histories[item.key],
                result=forecast(
                    histories[item.key],
                    today=today,
                    on_hand=item.on_hand,
                    incoming=incoming.get(item.key, 0),
                    lead_time_days=leads[item.key].days,
                    cover_days=cover_days,
                ),
            )
            for item in items
        ]

    # ---------------------------------------------------------- snapshot ---

    async def snapshot(self, today: date | None = None) -> dict[str, int]:
        """Store today's forecasts once, and announce items that just became at risk.

        Idempotent per day: a second run finds today's rows and does nothing.
        "Just became" compares with the latest earlier snapshot, so an item that
        stays at risk is announced once, not every morning.
        """
        tenant_id = require_tenant_id()
        today = today or business_date()
        done = await self.db.scalar(
            sa.select(DemandForecast.id).where(DemandForecast.as_of == today).limit(1)
        )
        if done is not None:
            return {"stored": 0, "announced": 0}
        previous_day = await self.db.scalar(
            sa.select(sa.func.max(DemandForecast.as_of)).where(DemandForecast.as_of < today)
        )
        was_at_risk: set[str] = set()
        if previous_day is not None:
            was_at_risk = set(
                (
                    await self.db.scalars(
                        sa.select(DemandForecast.item_key).where(
                            DemandForecast.as_of == previous_day, DemandForecast.at_risk.is_(True)
                        )
                    )
                ).all()
            )
        stored = announced = 0
        for row in await self.forecasts(today=today):
            result = row.result
            self.db.add(
                DemandForecast(
                    as_of=today,
                    product_id=row.item.product_id,
                    variant_id=row.item.variant_id,
                    item_key=row.item.key,
                    confidence=str(result.confidence),
                    rate_milli=result.rate_milli,
                    horizon_days=HORIZON_DAYS,
                    predicted_units=result.predicted_units(),
                    on_hand=result.on_hand,
                    incoming=result.incoming,
                    lead_time_days=result.lead_time_days,
                    reorder_point=result.reorder_point,
                    suggested_quantity=result.suggested_quantity,
                    stockout_on=result.stockout_on,
                    at_risk=result.at_risk,
                )
            )
            stored += 1
            if result.at_risk and row.item.key not in was_at_risk:
                key = f"forecast:stockout:{row.item.key}:{today.isoformat()}"
                seen = await self.db.scalar(
                    sa.select(OutboxEvent.id).where(OutboxEvent.dedupe_key == key)
                )
                if seen is None:
                    await enqueue(
                        self.db,
                        OutboxTopic.STOCKOUT_PREDICTED,
                        {
                            "product_id": str(row.item.product_id),
                            "variant_id": str(row.item.variant_id) if row.item.variant_id else None,
                            "stockout_on": result.stockout_on.isoformat()
                            if result.stockout_on
                            else None,
                            "suggested_quantity": result.suggested_quantity,
                        },
                        tenant_id=tenant_id,
                        dedupe_key=key,
                    )
                    announced += 1
        await self.db.flush()
        await self._prune(today)
        return {"stored": stored, "announced": announced}

    async def _prune(self, today: date) -> None:
        old = (
            await self.db.scalars(
                sa.select(DemandForecast)
                .where(DemandForecast.as_of < today - timedelta(days=KEEP_SNAPSHOT_DAYS))
                .limit(2_000)
            )
        ).all()
        for row in old:
            await self.db.delete(row)
        await self.db.flush()

    async def latest_snapshot(self) -> tuple[date | None, list[DemandForecast]]:
        day = await self.db.scalar(sa.select(sa.func.max(DemandForecast.as_of)))
        if day is None:
            return None, []
        rows = (
            await self.db.scalars(sa.select(DemandForecast).where(DemandForecast.as_of == day))
        ).all()
        return day, list(rows)

    # ---------------------------------------------------------- accuracy ---

    async def accuracy(self, today: date | None = None) -> dict[str, Any]:
        """How the forecast stored one horizon ago compares with what sold since.

        Only a stored snapshot can be scored honestly. Items without a forecast
        (insufficient history) are left out and counted.
        """
        today = today or business_date()
        as_of = await self.db.scalar(
            sa.select(sa.func.max(DemandForecast.as_of)).where(
                DemandForecast.as_of <= today - timedelta(days=HORIZON_DAYS)
            )
        )
        if as_of is None:
            return {"status": "NOT_ENOUGH_HISTORY", "as_of": None, "items": 0}
        rows = (
            await self.db.scalars(
                sa.select(DemandForecast).where(
                    DemandForecast.as_of == as_of, DemandForecast.predicted_units.is_not(None)
                )
            )
        ).all()
        # The snapshot of day D predicts D .. D + horizon − 1.
        start, _ = business_day_bounds(as_of)
        _, end = business_day_bounds(as_of + timedelta(days=HORIZON_DAYS - 1))
        sold = await self._sold_between(start, end)
        pairs = [(int(r.predicted_units or 0), sold.get(r.item_key, 0)) for r in rows]
        skipped = int(
            await self.db.scalar(
                sa.select(sa.func.count(DemandForecast.id)).where(
                    DemandForecast.as_of == as_of, DemandForecast.predicted_units.is_(None)
                )
            )
            or 0
        )
        if len(pairs) < MIN_SCORED_ITEMS:
            return {
                "status": "NOT_ENOUGH_HISTORY",
                "as_of": as_of,
                "items": len(pairs),
                "skipped": skipped,
            }
        error = wape(pairs)
        return {
            "status": "SCORED" if error is not None else "NO_SALES",
            "as_of": as_of,
            "horizon_days": HORIZON_DAYS,
            "items": len(pairs),
            "skipped": skipped,
            "predicted_units": sum(p for p, _ in pairs),
            "actual_units": sum(a for _, a in pairs),
            "error_bps": None if error is None else round(error * 10_000),
            "bias_units": sum(p - a for p, a in pairs),
        }

    async def _sold_between(self, start: datetime, end: datetime) -> dict[str, int]:
        rows = await self.db.execute(
            sa.select(
                StockMovement.product_id,
                StockMovement.variant_id,
                sa.func.sum(StockMovement.quantity_delta),
            )
            .where(
                StockMovement.reason.in_([_SALE, _UNSALE]),
                StockMovement.occurred_at >= start,
                StockMovement.occurred_at < end,
            )
            .group_by(StockMovement.product_id, StockMovement.variant_id)
        )
        return {item_key(p, v): max(0, -int(total or 0)) for p, v, total in rows}

    # ------------------------------------------------------ cash outlook ---

    async def cash_outlook(self, now: datetime | None = None) -> dict[str, Any]:
        """Expected COD in against supplier money going out, over the next weeks.

        Inflows are the money core's own COD forecast, unchanged. Outflows are
        what purchase orders say is owed to suppliers, by due date. A forecast:
        it is never a bank balance and never enters the financial ledger.
        """
        from app.money.cashflow import CashflowService

        moment = now or utc_now()
        today = business_date(at=moment)
        cash = await CashflowService(self.db).cashflow(as_of=moment)
        inflow = {window.key: window.amount_paisa for window in cash.forecast}

        out = {"overdue": 0, "next_7_days": 0, "days_8_to_14": 0, "later": 0, "no_due_date": 0}
        committed = 0
        rows = (
            await self.db.scalars(
                sa.select(PurchaseOrder).where(PurchaseOrder.status.not_in(("DRAFT",)))
            )
        ).all()
        for po in rows:
            owed = max(po.received_value_paisa - po.paid_paisa, 0)
            if po.status in OPEN_PO:
                # Ordered but not yet received: not owed yet, but spoken for.
                committed += max(po.total_paisa - max(po.received_value_paisa, po.paid_paisa), 0)
            if owed == 0:
                continue
            if po.payment_due_at is None:
                out["no_due_date"] += owed
                continue
            days = (business_date(at=ensure_utc(po.payment_due_at)) - today).days
            if days < 0:
                out["overdue"] += owed
            elif days <= 7:
                out["next_7_days"] += owed
            elif days <= 14:
                out["days_8_to_14"] += owed
            else:
                out["later"] += owed

        in_7 = inflow.get("next_7_days", 0)
        in_14 = in_7 + inflow.get("days_8_to_14", 0)
        out_7 = out["overdue"] + out["next_7_days"]
        out_14 = out_7 + out["days_8_to_14"]
        return {
            "as_of": today,
            "inflow": inflow,
            "outflow": out,
            "committed_on_open_orders_paisa": committed,
            "net_7_days_paisa": in_7 - out_7,
            "net_14_days_paisa": in_14 - out_14,
            "unplaced_inflow_paisa": inflow.get("past_expected", 0) + inflow.get("no_history", 0),
        }
