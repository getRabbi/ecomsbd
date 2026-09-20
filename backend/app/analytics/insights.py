"""Advanced Insights: an explanation layer over figures the shop already keeps.

Nothing here is a second source of business truth. Every number is read from
the service that owns it:

* revenue, profit and how measured they are — profit snapshots, through
  :class:`ProfitService` and :class:`AnalyticsService`;
* orders — :meth:`AnalyticsService.order_count`;
* delivered, RTO and their rates — :class:`RtoService`, the one RTO classifier;
* receivable, overdue, COD on the road, aging, payout delay and the forecast —
  :class:`CashflowService` (V2.2 receivables);
* cash received — the ledger's ``COD_SETTLED`` bucket, as cashflow reads it;
* discrepancies — :meth:`ReconciliationService.summary`, its item statuses and
  the case table (V2.2 reconciliation);
* stock — the stock columns the movement ledger maintains, and the ledger's
  own rows for when something last sold (V2.2 inventory).

What this module adds is arrangement only: a window and the one before it,
comparisons that refuse a meaningless base, per-product / courier / customer /
stock views, and deterministic "what changed" facts built from those figures.
No forecast of demand, no score and no ranking of couriers: a courier
scorecard lists facts side by side, alphabetically, and the seller decides.

Every query runs on the request's tenant-scoped session; there is no
cross-shop figure anywhere in this file.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.rto import (
    MIN_RATE_SAMPLE,
    NOT_LIVE_PROVIDERS,
    RTO_STATUSES,
    OutcomeCounts,
    ParcelOutcome,
    RtoService,
    rate_bps,
    statuses_for,
    trend_of,
)
from app.analytics.service import AnalyticsService, DayPoint
from app.consignments.models import Consignment
from app.core.clock import business_date, business_day_bounds, utc_now
from app.core.errors import ValidationError
from app.customers.models import Customer
from app.db.types import TZDateTime
from app.ledger.models import LedgerBucket
from app.ledger.service import LedgerService
from app.money.cashflow import Cashflow, CashflowService, CourierBalance
from app.money.service import AGING_BUCKETS
from app.notifications.smart import stuck_parcel_clause
from app.orders.models import Order
from app.products.models import Product, ProductVariant, StockMovement, StockMovementReason
from app.products.service import ProductService, StockSummary, low_stock_clause
from app.products.service import out_of_stock_clause as product_out_clause
from app.profit.models import ProfitQuality
from app.profit.service import ProfitService
from app.reconciliation.models import (
    DISCREPANCY_STATUSES,
    CaseStatus,
    ReconciliationCase,
    ReconciliationItem,
)
from app.reconciliation.service import ReconciliationService, ReconciliationSummary

__all__ = [
    "FAST_MOVING_MIN_UNITS",
    "MAX_RANGE_DAYS",
    "PRESET_DAYS",
    "SLOW_MOVING_DAYS",
    "Comparison",
    "Explanation",
    "ExplanationCode",
    "InsightsService",
    "InventoryFilter",
    "ProductCategory",
    "Window",
    "compare",
    "resolve_window",
]

# --------------------------------------------------------------------------- #
# Definitions — every threshold Insights applies, in one place
# --------------------------------------------------------------------------- #

#: The ranges the screens offer. A custom range is also accepted.
PRESET_DAYS: Final = (7, 30, 90)
#: The longest range one request may aggregate.
MAX_RANGE_DAYS: Final = 366
#: Up to this many days the trend is daily; beyond it, 7-day blocks.
DAILY_TREND_MAX_DAYS: Final = 92
#: A percentage change is stated only over a base at least this large. "+300%"
#: on one order last week is noise, not news.
MIN_COMPARISON_COUNT: Final = 5
MIN_COMPARISON_PAISA: Final = 100_000  # ৳1,000
#: Smaller moves than this are not called out as a change.
CHANGE_DEAD_BAND_BPS: Final = 500
#: "Mostly with Steadfast": one courier holds at least this share.
CONCENTRATION_BPS: Final = 5_000
#: A product holding at least this share of returned value is pointed out.
RETURN_SHARE_BPS: Final = 2_500
#: Slow-moving: in stock, in the catalogue for at least N days, and not one
#: unit booked out in the last N days. Fact, not forecast.
SLOW_MOVING_DAYS: Final = 30
SLOW_MOVING_MIN_DAYS: Final = 14
SLOW_MOVING_MAX_DAYS: Final = 180
#: Fast-moving: at least this many units booked out in the window.
FAST_MOVING_MIN_UNITS: Final = 10

#: The movement that is a sale leaving the shelf, and the one that undoes it.
_SALE: Final = str(StockMovementReason.BOOKED_DECREMENT)
_UNSALE: Final = str(StockMovementReason.CANCEL_RESTORE)
_DELIVERED_STATUSES: Final = tuple(statuses_for(ParcelOutcome.DELIVERED, ParcelOutcome.PARTIAL))
_OPEN_CASES: Final = (str(CaseStatus.OPEN), str(CaseStatus.IN_PROGRESS))
_CLOSED_CASES: Final = (str(CaseStatus.RESOLVED), str(CaseStatus.DISMISSED))


# --------------------------------------------------------------------------- #
# Windows and comparisons
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Window:
    """Inclusive Asia/Dhaka business dates."""

    since: date
    until: date

    @property
    def days(self) -> int:
        return (self.until - self.since).days + 1

    def previous(self) -> Window:
        """The equivalent period immediately before this one."""
        end = self.since - timedelta(days=1)
        return Window(end - timedelta(days=self.days - 1), end)

    def bounds(self) -> tuple[datetime, datetime]:
        """Half-open UTC ``[start, end)`` covering the business dates."""
        return business_day_bounds(self.since)[0], business_day_bounds(self.until)[1]


def resolve_window(
    *,
    days: int | None = None,
    since: date | None = None,
    until: date | None = None,
    today: date | None = None,
) -> Window:
    """A preset (7/30/90 days to today) or a bounded custom range."""
    today = today or business_date(at=utc_now())
    if since is not None or until is not None:
        if since is None or until is None:
            raise ValidationError("A custom range needs both since and until")
        if since > until:
            raise ValidationError("The range starts after it ends")
        if until > today:
            raise ValidationError("The range cannot end after today")
        if (until - since).days + 1 > MAX_RANGE_DAYS:
            raise ValidationError(
                f"A range can cover at most {MAX_RANGE_DAYS} days",
                details={"max_days": str(MAX_RANGE_DAYS)},
            )
        return Window(since, until)
    span = days or 30
    if span not in PRESET_DAYS:
        raise ValidationError(
            "Choose 7, 30 or 90 days, or a custom range",
            details={"allowed": ",".join(str(d) for d in PRESET_DAYS)},
        )
    return Window(today - timedelta(days=span - 1), today)


@dataclass(frozen=True, slots=True)
class Comparison:
    """A figure, the same figure one period earlier, and the change if stated."""

    current: int
    previous: int
    #: ``None`` when the earlier base is too small, zero or negative for a
    #: percentage to mean anything. Never a made-up 0% or 100%.
    change_bps: int | None


def compare(current: int, previous: int, *, floor: int) -> Comparison:
    change = None
    if previous > 0 and previous >= floor:
        change = round((current - previous) * 10_000 / previous)
    return Comparison(current=current, previous=previous, change_bps=change)


def _share(part: int, whole: int) -> int | None:
    return rate_bps(part, whole) if whole > 0 else None


# --------------------------------------------------------------------------- #
# "What changed" — deterministic facts, worded by the client catalogue
# --------------------------------------------------------------------------- #


class ExplanationCode(StrEnum):
    PROFIT_CHANGED = "PROFIT_CHANGED"
    PROFIT_CHANGED_WITH_RTO = "PROFIT_CHANGED_WITH_RTO"
    REVENUE_CHANGED = "REVENUE_CHANGED"
    ORDERS_CHANGED = "ORDERS_CHANGED"
    RTO_CHANGED = "RTO_CHANGED"
    OVERDUE_CONCENTRATED = "OVERDUE_CONCENTRATED"
    RECEIVABLE_AGE_SHARE = "RECEIVABLE_AGE_SHARE"
    RECEIVED_CHANGED = "RECEIVED_CHANGED"
    RETURN_VALUE_CONCENTRATED = "RETURN_VALUE_CONCENTRATED"
    COST_DATA_INCOMPLETE = "COST_DATA_INCOMPLETE"
    OPEN_CASES = "OPEN_CASES"
    SLOW_MOVING = "SLOW_MOVING"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    LOW_STOCK = "LOW_STOCK"
    NEW_CUSTOMERS = "NEW_CUSTOMERS"
    REPEAT_ORDER_SHARE = "REPEAT_ORDER_SHARE"


@dataclass(frozen=True, slots=True)
class Explanation:
    """A code and the figures that make it true. The client words it."""

    code: ExplanationCode
    params: dict[str, int | str | None] = field(default_factory=dict)


def _changed(comparison: Comparison) -> bool:
    return comparison.change_bps is not None and abs(comparison.change_bps) >= CHANGE_DEAD_BAND_BPS


# --------------------------------------------------------------------------- #
# Value objects
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class MoneyOverview:
    revenue: Comparison
    profit: Comparison
    #: Parcels behind ``profit`` by quality — actual / estimated / missing.
    quality: dict[str, int]
    received: Comparison
    receivable_paisa: int
    overdue_paisa: int
    in_transit_paisa: int
    open_case_count: int
    open_case_paisa: int
    discrepancy_count: int
    discrepancy_paisa: int


@dataclass(slots=True)
class StockOverview:
    summary: StockSummary
    slow_moving_items: int
    slow_moving_days: int


@dataclass(slots=True)
class Overview:
    window: Window
    previous: Window
    orders: Comparison
    delivered: Comparison
    rto: OutcomeCounts
    rto_previous: OutcomeCounts
    rto_trend: str
    money: MoneyOverview | None
    stock: StockOverview | None
    explanations: list[Explanation]


@dataclass(frozen=True, slots=True)
class TrendBucket:
    start: date
    end: date
    orders: int
    parcels: int
    revenue_paisa: int
    profit_paisa: int


@dataclass(slots=True)
class Trend:
    window: Window
    granularity: str  # day | week
    buckets: list[TrendBucket]
    quality: dict[str, int]


class ProductCategory(StrEnum):
    ALL = "all"
    TOP_REVENUE = "top_revenue"
    TOP_PROFIT = "top_profit"
    HIGH_RTO = "high_rto"
    LOW_STOCK = "low_stock"
    SLOW_MOVING = "slow_moving"
    FAST_MOVING = "fast_moving"


#: Categories that are ranked by money, so need money access.
MONEY_CATEGORIES: Final = frozenset({ProductCategory.TOP_REVENUE, ProductCategory.TOP_PROFIT})


@dataclass(slots=True)
class ProductInsight:
    product_id: uuid.UUID | None
    name: str
    sku: str | None = None
    parcels: int = 0
    units_delivered: int = 0
    revenue_paisa: int = 0
    profit_paisa: int = 0
    estimated_parcels: int = 0
    missing_parcels: int = 0
    rto: OutcomeCounts = field(default_factory=OutcomeCounts)
    rto_value_paisa: int = 0
    tracked: bool = False
    stock_on_hand: int | None = None
    stock_status: str = "NONE"  # OK | LOW | OUT | UNTRACKED | NONE (free text)
    units_booked: int = 0
    last_sale_at: datetime | None = None
    slow_moving: bool = False

    @property
    def quality(self) -> ProfitQuality | None:
        if not self.parcels:
            return None
        if self.missing_parcels:
            return ProfitQuality.MISSING
        if self.estimated_parcels:
            return ProfitQuality.ESTIMATED
        return ProfitQuality.ACTUAL

    @property
    def measurable_profit(self) -> bool:
        """A cost is unknown for some parcel: the profit is not a figure at all."""
        return self.parcels > 0 and not self.missing_parcels

    @property
    def margin_bps(self) -> int | None:
        if not self.measurable_profit or self.revenue_paisa <= 0:
            return None
        return round(self.profit_paisa * 10_000 / self.revenue_paisa)

    @property
    def fast_moving(self) -> bool:
        return self.units_booked >= FAST_MOVING_MIN_UNITS


@dataclass(slots=True)
class Page[T]:
    items: list[T]
    total: int
    offset: int
    limit: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total


@dataclass(slots=True)
class ProductInsights:
    window: Window
    slow_moving_days: int
    category: ProductCategory
    counts: dict[str, int]
    page: Page[ProductInsight]


@dataclass(slots=True)
class CourierScorecard:
    provider: str
    counts: OutcomeCounts = field(default_factory=OutcomeCounts)
    recent: OutcomeCounts = field(default_factory=OutcomeCounts)
    previous: OutcomeCounts = field(default_factory=OutcomeCounts)
    in_transit_now: int = 0
    stuck_now: int = 0
    balance: CourierBalance | None = None
    discrepancy_count: int = 0
    discrepancy_paisa: int = 0

    @property
    def trend(self) -> str:
        return trend_of(self.recent, self.previous)


@dataclass(slots=True)
class CourierInsights:
    window: Window
    items: list[CourierScorecard]
    excluded_providers: list[str]


@dataclass(frozen=True, slots=True)
class AgingRow:
    label: str
    min_days: int
    max_days: int | None
    count: int
    amount_paisa: int


@dataclass(slots=True)
class CashInsights:
    window: Window
    received: Comparison
    flow: Cashflow
    aging: list[AgingRow]
    couriers: list[CourierBalance]
    explanations: list[Explanation]


@dataclass(frozen=True, slots=True)
class CasePeriod:
    start: date
    end: date
    opened: int
    resolved: int


@dataclass(slots=True)
class ReconciliationInsights:
    window: Window
    summary: ReconciliationSummary
    open_by_kind: list[tuple[str, int, int]]
    periods: list[CasePeriod]
    by_courier: list[tuple[str, int, int]]
    explanations: list[Explanation]


@dataclass(slots=True)
class CustomerInsights:
    window: Window
    total_customers: int
    active: Comparison
    new: Comparison
    returning: int
    orders_with_customer: int
    repeat_orders: int
    repeat_customers: int
    multi_delivery_customers: int
    repeat_rto_customers: int
    explanations: list[Explanation]

    @property
    def repeat_order_rate_bps(self) -> int | None:
        return _share(self.repeat_orders, self.orders_with_customer)

    @property
    def sufficient(self) -> bool:
        return self.orders_with_customer >= MIN_RATE_SAMPLE


class InventoryFilter(StrEnum):
    ALL = "all"
    LOW_STOCK = "low_stock"
    OUT_OF_STOCK = "out_of_stock"
    SLOW_MOVING = "slow_moving"
    FAST_MOVING = "fast_moving"


@dataclass(slots=True)
class StockItem:
    """One sellable thing: a simple product, or one variant of a product."""

    product_id: uuid.UUID
    variant_id: uuid.UUID | None
    name: str
    variant_name: str | None
    sku: str | None
    stock_on_hand: int
    low_stock_threshold: int | None
    created_at: datetime | None
    units_booked: int = 0
    last_sale_at: datetime | None = None
    slow_moving: bool = False

    @property
    def is_out(self) -> bool:
        return self.stock_on_hand <= 0

    @property
    def is_low(self) -> bool:
        """The seller's own threshold, exactly as the stock summary counts it."""
        return (
            self.low_stock_threshold is not None and self.stock_on_hand <= self.low_stock_threshold
        )

    @property
    def status(self) -> str:
        if self.is_out:
            return "OUT"
        return "LOW" if self.is_low else "OK"

    @property
    def fast_moving(self) -> bool:
        return self.units_booked >= FAST_MOVING_MIN_UNITS


@dataclass(slots=True)
class InventoryInsights:
    window: Window
    slow_moving_days: int
    summary: StockSummary
    counts: dict[str, int]
    #: Units in and out of stock in the window, by movement reason.
    movement: list[tuple[str, int, int]]
    page: Page[StockItem]
    explanations: list[Explanation]


@dataclass(slots=True)
class _Movement:
    units_booked: int = 0
    last_sale_at: datetime | None = None


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #


class InsightsService:
    """Read-only. Composes the owning services; computes no business figure."""

    def __init__(self, session: AsyncSession) -> None:
        self._db = session
        self._analytics = AnalyticsService(session)
        self._profit = ProfitService(session)
        self._rto = RtoService(session)
        self._cashflow = CashflowService(session)
        self._ledger = LedgerService(session)
        self._reconciliation = ReconciliationService(session)
        self._products = ProductService(session)

    # --------------------------------------------------------- overview --

    async def overview(
        self,
        window: Window,
        *,
        money: bool,
        stock: bool,
        slow_days: int = SLOW_MOVING_DAYS,
    ) -> Overview:
        previous = window.previous()
        orders = compare(
            await self._analytics.order_count(window.since, window.until),
            await self._analytics.order_count(previous.since, previous.until),
            floor=MIN_COMPARISON_COUNT,
        )
        rto_now = await self._rto.window_counts(days=window.days, ending=window.until)
        rto_before = await self._rto.window_counts(days=previous.days, ending=previous.until)
        delivered = compare(
            rto_now.delivered + rto_now.partial,
            rto_before.delivered + rto_before.partial,
            floor=MIN_COMPARISON_COUNT,
        )
        rto_trend = trend_of(rto_now, rto_before)

        money_part: MoneyOverview | None = None
        balances: list[CourierBalance] = []
        if money:
            now_totals = await self._profit.totals(since=window.since, until=window.until)
            before_totals = await self._profit.totals(since=previous.since, until=previous.until)
            quality = await self._profit.quality_breakdown(since=window.since, until=window.until)
            balances = await self._cashflow.courier_balances()
            discrepancies = await self._discrepancies_by_provider(window)
            open_count, open_paisa = await self._open_cases()
            money_part = MoneyOverview(
                revenue=compare(
                    now_totals["realized_revenue_paisa"],
                    before_totals["realized_revenue_paisa"],
                    floor=MIN_COMPARISON_PAISA,
                ),
                profit=compare(
                    now_totals["contribution_profit_paisa"],
                    before_totals["contribution_profit_paisa"],
                    floor=MIN_COMPARISON_PAISA,
                ),
                quality={str(key): count for key, count in quality.items()},
                received=await self._received(window, previous),
                receivable_paisa=sum(row.outstanding_paisa for row in balances),
                overdue_paisa=sum(row.overdue_paisa for row in balances),
                in_transit_paisa=sum(row.in_transit_paisa for row in balances),
                open_case_count=open_count,
                open_case_paisa=open_paisa,
                discrepancy_count=sum(count for count, _ in discrepancies.values()),
                discrepancy_paisa=sum(amount for _, amount in discrepancies.values()),
            )

        stock_part: StockOverview | None = None
        if stock:
            items = await self._stock_items(window, slow_days)
            stock_part = StockOverview(
                summary=await self._products.stock_summary(),
                slow_moving_items=sum(1 for item in items if item.slow_moving),
                slow_moving_days=slow_days,
            )

        explanations: list[Explanation] = []
        rto_moved = rto_trend in ("UP", "DOWN")
        rto_params: dict[str, int | str | None] = {
            "rto_from_bps": rto_before.rto_rate_bps,
            "rto_to_bps": rto_now.rto_rate_bps,
        }
        if money_part is not None and _changed(money_part.profit):
            params: dict[str, int | str | None] = {
                "change_bps": money_part.profit.change_bps,
                "days": window.days,
            }
            if rto_moved:
                explanations.append(
                    Explanation(ExplanationCode.PROFIT_CHANGED_WITH_RTO, params | rto_params)
                )
            else:
                explanations.append(Explanation(ExplanationCode.PROFIT_CHANGED, params))
        if money_part is not None and _changed(money_part.revenue):
            explanations.append(
                Explanation(
                    ExplanationCode.REVENUE_CHANGED,
                    {"change_bps": money_part.revenue.change_bps, "days": window.days},
                )
            )
        if _changed(orders):
            explanations.append(
                Explanation(
                    ExplanationCode.ORDERS_CHANGED,
                    {"change_bps": orders.change_bps, "days": window.days},
                )
            )
        already_said = any(e.code is ExplanationCode.PROFIT_CHANGED_WITH_RTO for e in explanations)
        if rto_moved and not already_said:
            explanations.append(
                Explanation(ExplanationCode.RTO_CHANGED, rto_params | {"days": window.days})
            )
        if money_part is not None:
            concentrated = _overdue_concentration(balances)
            if concentrated is not None:
                explanations.append(concentrated)
        returned_value = await self._return_value_concentration(window)
        if returned_value is not None:
            explanations.append(returned_value)
        if money_part is not None:
            missing = money_part.quality.get(str(ProfitQuality.MISSING), 0)
            if missing:
                explanations.append(
                    Explanation(
                        ExplanationCode.COST_DATA_INCOMPLETE,
                        {"missing": missing, "parcels": sum(money_part.quality.values())},
                    )
                )
            if money_part.open_case_count:
                explanations.append(
                    Explanation(
                        ExplanationCode.OPEN_CASES,
                        {
                            "count": money_part.open_case_count,
                            "amount_paisa": money_part.open_case_paisa,
                        },
                    )
                )
        if stock_part is not None:
            explanations.extend(_stock_explanations(stock_part))

        return Overview(
            window=window,
            previous=previous,
            orders=orders,
            delivered=delivered,
            rto=rto_now,
            rto_previous=rto_before,
            rto_trend=rto_trend,
            money=money_part,
            stock=stock_part,
            explanations=explanations,
        )

    # ------------------------------------------------------------ trend --

    async def trend(self, window: Window) -> Trend:
        """Orders, settled parcels, revenue and profit over the window.

        Daily up to :data:`DAILY_TREND_MAX_DAYS`, then 7-day blocks from the
        window's first day. Empty days are kept as zeroes so a quiet week reads
        as quiet rather than compressing the axis.
        """
        series: list[DayPoint] = await self._analytics.daily_series(window.since, window.until)
        orders = await self._analytics.orders_by_day(window.since, window.until)
        quality = await self._profit.quality_breakdown(since=window.since, until=window.until)
        step = 1 if window.days <= DAILY_TREND_MAX_DAYS else 7

        buckets: list[TrendBucket] = []
        for offset in range(0, len(series), step):
            chunk = series[offset : offset + step]
            buckets.append(
                TrendBucket(
                    start=chunk[0].business_date,
                    end=chunk[-1].business_date,
                    orders=sum(orders.get(point.business_date, 0) for point in chunk),
                    parcels=sum(point.parcel_count for point in chunk),
                    revenue_paisa=sum(point.realized_revenue_paisa for point in chunk),
                    profit_paisa=sum(point.contribution_profit_paisa for point in chunk),
                )
            )
        return Trend(
            window=window,
            granularity="day" if step == 1 else "week",
            buckets=buckets,
            quality={str(key): count for key, count in quality.items()},
        )

    # --------------------------------------------------------- products --

    async def products(
        self,
        window: Window,
        *,
        category: ProductCategory = ProductCategory.ALL,
        money: bool,
        slow_days: int = SLOW_MOVING_DAYS,
        offset: int = 0,
        limit: int = 20,
    ) -> ProductInsights:
        rows = await self._product_rows(window, slow_days)
        filters = {cat: _product_filter(cat) for cat in ProductCategory}
        counts = {
            str(cat): sum(1 for row in rows if filters[cat](row))
            for cat in ProductCategory
            if money or cat not in MONEY_CATEGORIES
        }
        chosen = sorted(
            (row for row in rows if filters[category](row)),
            key=_product_sort(category, money=money),
        )
        return ProductInsights(
            window=window,
            slow_moving_days=slow_days,
            category=category,
            counts=counts,
            page=Page(
                items=chosen[offset : offset + limit],
                total=len(chosen),
                offset=offset,
                limit=limit,
            ),
        )

    async def _product_rows(self, window: Window, slow_days: int) -> list[ProductInsight]:
        """One row per product: sales, RTO and stock, each from its owner."""
        found: dict[tuple[uuid.UUID | None, str | None], ProductInsight] = {}

        def row(product_id: uuid.UUID | None, name: str) -> ProductInsight:
            key = (product_id, None if product_id is not None else name)
            if key not in found:
                found[key] = ProductInsight(product_id=product_id, name=name)
            return found[key]

        for line in await self._analytics.product_economics(since=window.since, until=window.until):
            entry = row(line.product_id, line.product_name)
            entry.parcels = line.parcel_count
            entry.units_delivered = line.units_delivered
            entry.revenue_paisa = line.revenue_paisa
            entry.profit_paisa = line.profit_paisa
            entry.estimated_parcels = line.estimated_parcels
            entry.missing_parcels = line.missing_parcels

        for product in await self._rto.products(days=window.days, today=window.until):
            entry = row(product.product_id, product.product_name)
            entry.rto = product.counts
            entry.rto_value_paisa = product.rto_value_paisa

        seen = [key[0] for key in found if key[0] is not None]
        catalogue = await self._db.execute(
            sa.select(
                Product.id,
                Product.name,
                Product.sku,
                Product.stock_tracking_enabled,
                Product.stock_on_hand,
                Product.created_at,
                low_stock_clause(),
                product_out_clause(),
            ).where(
                sa.or_(
                    sa.and_(Product.archived_at.is_(None), Product.is_active.is_(True)),
                    Product.id.in_(seen) if seen else sa.false(),
                )
            )
        )
        movement = await self._movement(window, slow_days, by_variant=False)
        slow_start = _slow_start(slow_days)
        for product_id, name, sku, tracked, on_hand, created_at, low, out in catalogue:
            entry = row(product_id, name)
            entry.name = name
            entry.sku = sku
            entry.tracked = bool(tracked)
            moved = movement.get((product_id, None), _Movement())
            entry.units_booked = moved.units_booked
            entry.last_sale_at = moved.last_sale_at
            if not tracked:
                entry.stock_status = "UNTRACKED"
                continue
            entry.stock_on_hand = int(on_hand)
            entry.stock_status = "OUT" if out else "LOW" if low else "OK"
            entry.slow_moving = _is_slow(int(on_hand), created_at, moved.last_sale_at, slow_start)
        return list(found.values())

    # --------------------------------------------------------- couriers --

    async def couriers(self, window: Window, *, money: bool) -> CourierInsights:
        """Facts per courier, alphabetically. No ranking, no "best courier"."""
        cards: dict[str, CourierScorecard] = {}

        def card(provider: str) -> CourierScorecard:
            if provider not in cards:
                cards[provider] = CourierScorecard(provider=provider)
            return cards[provider]

        for courier in await self._rto.couriers(days=window.days, today=window.until):
            entry = card(courier.provider)
            entry.counts = courier.counts
            entry.recent = courier.recent
            entry.previous = courier.previous
            entry.in_transit_now = courier.in_transit_now

        stuck = await self._db.execute(
            sa.select(Consignment.provider, sa.func.count(Consignment.id))
            .where(*stuck_parcel_clause(utc_now()))
            .group_by(Consignment.provider)
        )
        for provider, count in stuck:
            if provider not in NOT_LIVE_PROVIDERS:
                card(provider).stuck_now = int(count)

        if money:
            for balance in await self._cashflow.courier_balances():
                if balance.provider not in NOT_LIVE_PROVIDERS:
                    card(balance.provider).balance = balance
            for provider, (count, amount) in (
                await self._discrepancies_by_provider(window)
            ).items():
                if provider not in NOT_LIVE_PROVIDERS:
                    entry = card(provider)
                    entry.discrepancy_count, entry.discrepancy_paisa = count, amount

        return CourierInsights(
            window=window,
            items=[cards[provider] for provider in sorted(cards)],
            excluded_providers=sorted(NOT_LIVE_PROVIDERS),
        )

    # ------------------------------------------------------------- cash --

    async def cash(self, window: Window) -> CashInsights:
        """Module 2's cashflow, arranged: facts first, the forecast labelled."""
        previous = window.previous()
        flow = await self._cashflow.cashflow(since=window.since, until=window.until)
        balances = await self._cashflow.courier_balances()
        totals = {bucket.label: [0, 0] for bucket in AGING_BUCKETS}
        for balance in balances:
            for bucket, count, amount in balance.aging:
                totals[bucket.label][0] += count
                totals[bucket.label][1] += amount
        aging = [
            AgingRow(
                label=bucket.label,
                min_days=bucket.min_days,
                max_days=bucket.max_days,
                count=totals[bucket.label][0],
                amount_paisa=totals[bucket.label][1],
            )
            for bucket in AGING_BUCKETS
        ]
        received = compare(
            flow.received_paisa,
            await self._received_total(previous),
            floor=MIN_COMPARISON_PAISA,
        )

        explanations: list[Explanation] = []
        if _changed(received):
            explanations.append(
                Explanation(
                    ExplanationCode.RECEIVED_CHANGED,
                    {"change_bps": received.change_bps, "days": window.days},
                )
            )
        concentrated = _overdue_concentration(balances)
        if concentrated is not None:
            explanations.append(concentrated)
        aged_total = sum(row.amount_paisa for row in aging)
        if aged_total > 0:
            largest = max(aging, key=lambda row: (row.amount_paisa, -row.min_days))
            explanations.append(
                Explanation(
                    ExplanationCode.RECEIVABLE_AGE_SHARE,
                    {
                        "bucket": largest.label,
                        "min_days": largest.min_days,
                        "max_days": largest.max_days,
                        "share_bps": _share(largest.amount_paisa, aged_total),
                    },
                )
            )
        return CashInsights(
            window=window,
            received=received,
            flow=flow,
            aging=aging,
            couriers=balances,
            explanations=explanations,
        )

    # --------------------------------------------------- reconciliation --

    async def reconciliation(self, window: Window) -> ReconciliationInsights:
        """Module 1's summary and cases, over the window's settlement dates."""
        summary = await self._reconciliation.summary(date_from=window.since, date_to=window.until)

        kinds = await self._db.execute(
            sa.select(
                ReconciliationCase.kind,
                sa.func.count(ReconciliationCase.id),
                sa.func.coalesce(sa.func.sum(ReconciliationCase.amount_paisa), 0),
            )
            .where(ReconciliationCase.status.in_(_OPEN_CASES))
            .group_by(ReconciliationCase.kind)
        )
        open_by_kind = sorted(
            ((str(kind), int(count), int(amount or 0)) for kind, count, amount in kinds),
            key=lambda row: (-row[1], row[0]),
        )

        discrepancies = await self._discrepancies_by_provider(window)
        by_courier = [
            (provider, count, amount) for provider, (count, amount) in sorted(discrepancies.items())
        ]

        explanations: list[Explanation] = []
        if summary.open_cases:
            explanations.append(
                Explanation(
                    ExplanationCode.OPEN_CASES,
                    {"count": summary.open_cases, "amount_paisa": summary.open_case_paisa},
                )
            )
        return ReconciliationInsights(
            window=window,
            summary=summary,
            open_by_kind=open_by_kind,
            periods=await self._case_periods(window),
            by_courier=by_courier,
            explanations=explanations,
        )

    async def _case_periods(self, window: Window) -> list[CasePeriod]:
        """Cases opened and closed per period: daily to two weeks, else weekly."""
        step = 1 if window.days <= 14 else 7
        blocks: list[tuple[date, date, datetime, datetime]] = []
        day = window.since
        while day <= window.until:
            end_day = min(window.until, day + timedelta(days=step - 1))
            blocks.append(
                (day, end_day, business_day_bounds(day)[0], business_day_bounds(end_day)[1])
            )
            day = end_day + timedelta(days=1)

        start, end = window.bounds()

        def counts(column: Any, *where: sa.ColumnElement[bool]) -> sa.Select[Any]:
            return sa.select(
                *(
                    sa.func.coalesce(
                        sa.func.sum(sa.case(((column >= lo) & (column < hi), 1), else_=0)), 0
                    )
                    for _, _, lo, hi in blocks
                )
            ).where(column >= start, column < end, *where)

        opened = (await self._db.execute(counts(ReconciliationCase.opened_at))).one()
        resolved = (
            await self._db.execute(
                counts(
                    ReconciliationCase.resolved_at,
                    ReconciliationCase.status.in_(_CLOSED_CASES),
                )
            )
        ).one()
        return [
            CasePeriod(start=block[0], end=block[1], opened=int(o or 0), resolved=int(r or 0))
            for block, o, r in zip(blocks, opened, resolved, strict=True)
        ]

    # -------------------------------------------------------- customers --

    async def customers(self, window: Window) -> CustomerInsights:
        """This shop's own customers, as counts. No names, no phones, no scores."""
        previous = window.previous()
        total = await self._db.scalar(
            sa.select(sa.func.count(Customer.id)).where(Customer.deleted_at.is_(None))
        )
        active, new, orders, _ = await self._customer_window(window)
        active_before, new_before, _, _ = await self._customer_window(previous)

        def having(statuses: tuple[str, ...]) -> sa.Select:
            per_customer = (
                sa.select(Order.customer_id)
                .join(Consignment, Consignment.order_id == Order.id)
                .where(Order.customer_id.is_not(None), Consignment.status.in_(statuses))
                .group_by(Order.customer_id)
                .having(sa.func.count(sa.distinct(Consignment.id)) >= 2)
                .subquery()
            )
            return sa.select(sa.func.count()).select_from(per_customer)

        repeat = (
            sa.select(Order.customer_id)
            .where(Order.customer_id.is_not(None))
            .group_by(Order.customer_id)
            .having(sa.func.count(Order.id) >= 2)
            .subquery()
        )
        insights = CustomerInsights(
            window=window,
            total_customers=int(total or 0),
            active=compare(active, active_before, floor=MIN_COMPARISON_COUNT),
            new=compare(new, new_before, floor=MIN_COMPARISON_COUNT),
            returning=active - new,
            orders_with_customer=orders,
            # Every order in the window is either one new customer's first order
            # or a repeat, so this is exact rather than estimated.
            repeat_orders=max(0, orders - new),
            repeat_customers=int(
                await self._db.scalar(sa.select(sa.func.count()).select_from(repeat)) or 0
            ),
            multi_delivery_customers=int(await self._db.scalar(having(_DELIVERED_STATUSES)) or 0),
            repeat_rto_customers=int(await self._db.scalar(having(RTO_STATUSES)) or 0),
            explanations=[],
        )
        if new:
            insights.explanations.append(
                Explanation(ExplanationCode.NEW_CUSTOMERS, {"count": new, "days": window.days})
            )
        if insights.sufficient and insights.repeat_order_rate_bps is not None:
            insights.explanations.append(
                Explanation(
                    ExplanationCode.REPEAT_ORDER_SHARE,
                    {"share_bps": insights.repeat_order_rate_bps, "days": window.days},
                )
            )
        return insights

    async def _customer_window(self, window: Window) -> tuple[int, int, int, int]:
        """``(active, new, orders, orders_from_returning)`` for one window."""
        first = (
            sa.select(
                Order.customer_id.label("customer_id"),
                sa.func.min(Order.business_date).label("first_day"),
            )
            .where(Order.customer_id.is_not(None))
            .group_by(Order.customer_id)
            .subquery()
        )
        placed = (
            sa.select(
                Order.customer_id.label("customer_id"),
                sa.func.count(Order.id).label("orders"),
            )
            .where(
                Order.customer_id.is_not(None),
                Order.business_date >= window.since,
                Order.business_date <= window.until,
            )
            .group_by(Order.customer_id)
            .subquery()
        )
        is_new = first.c.first_day >= window.since
        row = (
            await self._db.execute(
                sa.select(
                    sa.func.count(),
                    sa.func.coalesce(sa.func.sum(sa.case((is_new, 1), else_=0)), 0),
                    sa.func.coalesce(sa.func.sum(placed.c.orders), 0),
                    sa.func.coalesce(sa.func.sum(sa.case((is_new, 0), else_=placed.c.orders)), 0),
                ).select_from(placed.join(first, first.c.customer_id == placed.c.customer_id))
            )
        ).one()
        return int(row[0]), int(row[1]), int(row[2]), int(row[3])

    # -------------------------------------------------------- inventory --

    async def inventory(
        self,
        window: Window,
        *,
        filter_by: InventoryFilter = InventoryFilter.ALL,
        slow_days: int = SLOW_MOVING_DAYS,
        offset: int = 0,
        limit: int = 20,
    ) -> InventoryInsights:
        items = await self._stock_items(window, slow_days)
        predicates = {
            InventoryFilter.ALL: lambda item: True,
            InventoryFilter.LOW_STOCK: lambda item: item.is_low,
            InventoryFilter.OUT_OF_STOCK: lambda item: item.is_out,
            InventoryFilter.SLOW_MOVING: lambda item: item.slow_moving,
            InventoryFilter.FAST_MOVING: lambda item: item.fast_moving,
        }
        counts = {
            str(key): sum(1 for item in items if predicate(item))
            for key, predicate in predicates.items()
        }
        sort_keys = {
            InventoryFilter.ALL: lambda i: (
                i.name.lower(),
                i.variant_name or "",
                str(i.variant_id),
            ),
            InventoryFilter.LOW_STOCK: lambda i: (
                i.stock_on_hand,
                i.name.lower(),
                str(i.variant_id),
            ),
            InventoryFilter.OUT_OF_STOCK: lambda i: (
                i.stock_on_hand,
                i.name.lower(),
                str(i.variant_id),
            ),
            InventoryFilter.SLOW_MOVING: lambda i: (
                -i.stock_on_hand,
                i.name.lower(),
                str(i.variant_id),
            ),
            InventoryFilter.FAST_MOVING: lambda i: (
                -i.units_booked,
                i.name.lower(),
                str(i.variant_id),
            ),
        }
        chosen = sorted(
            (item for item in items if predicates[filter_by](item)), key=sort_keys[filter_by]
        )

        start, end = window.bounds()
        rows = await self._db.execute(
            sa.select(
                StockMovement.reason,
                sa.func.coalesce(
                    sa.func.sum(
                        sa.case(
                            (StockMovement.quantity_delta > 0, StockMovement.quantity_delta),
                            else_=0,
                        )
                    ),
                    0,
                ),
                sa.func.coalesce(
                    sa.func.sum(
                        sa.case(
                            (StockMovement.quantity_delta < 0, -StockMovement.quantity_delta),
                            else_=0,
                        )
                    ),
                    0,
                ),
            )
            .where(StockMovement.occurred_at >= start, StockMovement.occurred_at < end)
            .group_by(StockMovement.reason)
        )
        movement = sorted((str(reason), int(i), int(o)) for reason, i, o in rows)

        summary = await self._products.stock_summary()
        stock_overview = StockOverview(
            summary=summary,
            slow_moving_items=counts[str(InventoryFilter.SLOW_MOVING)],
            slow_moving_days=slow_days,
        )
        return InventoryInsights(
            window=window,
            slow_moving_days=slow_days,
            summary=summary,
            counts=counts,
            movement=movement,
            page=Page(
                items=chosen[offset : offset + limit], total=len(chosen), offset=offset, limit=limit
            ),
            explanations=_stock_explanations(stock_overview),
        )

    async def _stock_items(self, window: Window, slow_days: int) -> list[StockItem]:
        """Every tracked, live simple product and active variant, with movement."""
        live = (
            Product.archived_at.is_(None),
            Product.is_active.is_(True),
            Product.stock_tracking_enabled.is_(True),
        )
        items: list[StockItem] = []
        simple = await self._db.execute(
            sa.select(
                Product.id,
                Product.name,
                Product.sku,
                Product.stock_on_hand,
                Product.low_stock_threshold,
                Product.created_at,
            ).where(*live, Product.has_variants.is_(False))
        )
        for product_id, name, sku, on_hand, threshold, created_at in simple:
            items.append(
                StockItem(
                    product_id=product_id,
                    variant_id=None,
                    name=name,
                    variant_name=None,
                    sku=sku,
                    stock_on_hand=int(on_hand),
                    low_stock_threshold=threshold,
                    created_at=created_at,
                )
            )
        variants = await self._db.execute(
            sa.select(
                Product.id,
                ProductVariant.id,
                Product.name,
                ProductVariant.name,
                ProductVariant.sku,
                ProductVariant.stock_on_hand,
                ProductVariant.low_stock_threshold,
                ProductVariant.created_at,
            )
            .join(Product, Product.id == ProductVariant.product_id)
            .where(*live, ProductVariant.is_active.is_(True))
        )
        for (
            product_id,
            variant_id,
            name,
            variant_name,
            sku,
            on_hand,
            threshold,
            created,
        ) in variants:
            items.append(
                StockItem(
                    product_id=product_id,
                    variant_id=variant_id,
                    name=name,
                    variant_name=variant_name,
                    sku=sku,
                    stock_on_hand=int(on_hand),
                    low_stock_threshold=threshold,
                    created_at=created,
                )
            )

        movement = await self._movement(window, slow_days, by_variant=True)
        slow_start = _slow_start(slow_days)
        for item in items:
            moved = movement.get((item.product_id, item.variant_id), _Movement())
            item.units_booked = moved.units_booked
            item.last_sale_at = moved.last_sale_at
            item.slow_moving = _is_slow(
                item.stock_on_hand, item.created_at, moved.last_sale_at, slow_start
            )
        return items

    async def _movement(
        self, window: Window, slow_days: int, *, by_variant: bool
    ) -> dict[tuple[uuid.UUID, uuid.UUID | None], _Movement]:
        """Units booked out in the window, and the last sale, from the stock ledger.

        Units are net of cancellations restored in the same window. The last
        sale is read only as far back as needed to answer "sold in the last N
        days?", which bounds the scan however long the ledger grows.
        """
        start, end = window.bounds()
        lookback = min(start, _slow_start(slow_days))
        in_window = (StockMovement.occurred_at >= start) & (StockMovement.occurred_at < end)
        keys = [StockMovement.product_id] + ([StockMovement.variant_id] if by_variant else [])
        rows = await self._db.execute(
            sa.select(
                *keys,
                sa.func.coalesce(
                    sa.func.sum(sa.case((in_window, StockMovement.quantity_delta), else_=0)), 0
                ),
                sa.func.max(
                    sa.case(
                        (StockMovement.reason == _SALE, StockMovement.occurred_at), else_=sa.null()
                    ),
                    type_=TZDateTime(),
                ),
            )
            .where(
                StockMovement.reason.in_([_SALE, _UNSALE]),
                StockMovement.occurred_at >= lookback,
            )
            .group_by(*keys)
        )
        found: dict[tuple[uuid.UUID, uuid.UUID | None], _Movement] = {}
        for row in rows:
            product_id = row[0]
            variant_id = row[1] if by_variant else None
            net, last_sale = row[-2], row[-1]
            found[(product_id, variant_id)] = _Movement(
                units_booked=max(0, -int(net or 0)), last_sale_at=last_sale
            )
        return found

    # -------------------------------------------------------- internals --

    async def _received(self, window: Window, previous: Window) -> Comparison:
        return compare(
            await self._received_total(window),
            await self._received_total(previous),
            floor=MIN_COMPARISON_PAISA,
        )

    async def _received_total(self, window: Window) -> int:
        """COD that arrived, from the ledger — the same bucket cashflow reads."""
        balances = await self._ledger.balances(since=window.since, until=window.until)
        return balances[LedgerBucket.COD_SETTLED].net_paisa

    async def _open_cases(self) -> tuple[int, int]:
        count, amount = (
            await self._db.execute(
                sa.select(
                    sa.func.count(ReconciliationCase.id),
                    sa.func.coalesce(sa.func.sum(ReconciliationCase.amount_paisa), 0),
                ).where(ReconciliationCase.status.in_(_OPEN_CASES))
            )
        ).one()
        return int(count), int(amount or 0)

    async def _discrepancies_by_provider(self, window: Window) -> dict[str, tuple[int, int]]:
        """Items in reconciliation's own discrepancy statuses, per courier.

        The amount is the size of each difference, so an overpayment and an
        underpayment of ৳500 add to ৳1,000 of disagreement rather than cancel.
        """
        rows = await self._db.execute(
            sa.select(
                ReconciliationItem.provider,
                sa.func.count(ReconciliationItem.id),
                sa.func.coalesce(
                    sa.func.sum(
                        sa.func.abs(sa.func.coalesce(ReconciliationItem.difference_paisa, 0))
                    ),
                    0,
                ),
            )
            .where(
                ReconciliationItem.status.in_([str(status) for status in DISCREPANCY_STATUSES]),
                ReconciliationItem.settlement_date >= window.since,
                ReconciliationItem.settlement_date <= window.until,
            )
            .group_by(ReconciliationItem.provider)
        )
        return {str(provider): (int(count), int(amount or 0)) for provider, count, amount in rows}

    async def _return_value_concentration(self, window: Window) -> Explanation | None:
        products = await self._rto.products(days=window.days, today=window.until)
        # One parcel can contain many products; sample size is parcels, not lines.
        returned = (await self._rto.window_counts(days=window.days, ending=window.until)).rto
        total_value = sum(product.rto_value_paisa for product in products)
        # A share of returned value over a handful of returns is not a pattern.
        if total_value <= 0 or returned < MIN_RATE_SAMPLE:
            return None
        top = max(products, key=lambda p: (p.rto_value_paisa, p.product_name))
        share = _share(top.rto_value_paisa, total_value)
        if share is None or share < RETURN_SHARE_BPS:
            return None
        return Explanation(
            ExplanationCode.RETURN_VALUE_CONCENTRATED,
            {"product": top.product_name, "share_bps": share, "rto_count": top.counts.rto},
        )


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


def _slow_start(slow_days: int) -> datetime:
    return utc_now() - timedelta(days=slow_days)


def _is_slow(
    on_hand: int, created_at: datetime | None, last_sale: datetime | None, slow_start: datetime
) -> bool:
    """Stock on the shelf, listed for N days, and nothing booked out in N days."""
    if on_hand <= 0:
        return False
    if created_at is not None and created_at > slow_start:
        return False
    return last_sale is None or last_sale < slow_start


def _overdue_concentration(balances: list[CourierBalance]) -> Explanation | None:
    total = sum(row.overdue_paisa for row in balances)
    if total <= 0:
        return None
    top = max(balances, key=lambda row: (row.overdue_paisa, row.provider))
    share = _share(top.overdue_paisa, total)
    if share is None or share < CONCENTRATION_BPS:
        return None
    return Explanation(
        ExplanationCode.OVERDUE_CONCENTRATED,
        {
            "provider": top.provider,
            "amount_paisa": total,
            "courier_paisa": top.overdue_paisa,
            "share_bps": share,
        },
    )


def _stock_explanations(stock: StockOverview) -> list[Explanation]:
    found: list[Explanation] = []
    if stock.slow_moving_items:
        found.append(
            Explanation(
                ExplanationCode.SLOW_MOVING,
                {"count": stock.slow_moving_items, "days": stock.slow_moving_days},
            )
        )
    if stock.summary.out_of_stock_items:
        found.append(
            Explanation(ExplanationCode.OUT_OF_STOCK, {"count": stock.summary.out_of_stock_items})
        )
    if stock.summary.low_stock_items:
        found.append(
            Explanation(ExplanationCode.LOW_STOCK, {"count": stock.summary.low_stock_items})
        )
    return found


def _product_filter(category: ProductCategory):  # type: ignore[no-untyped-def]
    if category is ProductCategory.TOP_REVENUE:
        return lambda row: row.revenue_paisa > 0
    if category is ProductCategory.TOP_PROFIT:
        return lambda row: row.measurable_profit and row.profit_paisa > 0
    if category is ProductCategory.HIGH_RTO:
        # Section 24: no negative ranking on a thin sample.
        return lambda row: row.rto.sufficient and row.rto.rto > 0
    if category is ProductCategory.LOW_STOCK:
        return lambda row: row.stock_status in ("LOW", "OUT")
    if category is ProductCategory.SLOW_MOVING:
        return lambda row: row.slow_moving
    if category is ProductCategory.FAST_MOVING:
        return lambda row: row.fast_moving
    return lambda row: row.parcels > 0 or row.rto.completed > 0 or row.product_id is not None


def _product_sort(category: ProductCategory, *, money: bool):  # type: ignore[no-untyped-def]
    def tie(row: ProductInsight) -> tuple[str, str]:
        return (row.name.lower(), str(row.product_id))

    if category is ProductCategory.TOP_REVENUE:
        return lambda row: (-row.revenue_paisa, *tie(row))
    if category is ProductCategory.TOP_PROFIT:
        return lambda row: (-row.profit_paisa, *tie(row))
    if category is ProductCategory.HIGH_RTO:
        return lambda row: (-(row.rto.rto_rate_bps or 0), -row.rto.rto, *tie(row))
    if category is ProductCategory.LOW_STOCK:
        return lambda row: (row.stock_on_hand or 0, *tie(row))
    if category is ProductCategory.SLOW_MOVING:
        return lambda row: (-(row.stock_on_hand or 0), *tie(row))
    if category is ProductCategory.FAST_MOVING:
        return lambda row: (-row.units_booked, *tie(row))
    if money:
        return lambda row: (-row.revenue_paisa, -row.units_booked, *tie(row))
    return lambda row: (-row.units_delivered, -row.units_booked, *tie(row))
