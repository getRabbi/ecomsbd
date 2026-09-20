"""Advanced Insights over HTTP (``/v1/analytics/insights/*``).

Eight read-only endpoints, one per screen section. Each resolves the same
window (7/30/90 days or a bounded custom range, Asia/Dhaka business dates),
aggregates on the server and returns only what the member may see.

RBAC is not relaxed because this is "analytics". Operational figures follow
the permission their own screen needs (orders, products, customers); money
follows ``money.view`` and the plan's profit history, and is returned as
``null`` with ``money_locked`` saying why — never as a zero.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.analytics.insights import (
    FAST_MOVING_MIN_UNITS,
    MONEY_CATEGORIES,
    SLOW_MOVING_DAYS,
    SLOW_MOVING_MAX_DAYS,
    SLOW_MOVING_MIN_DAYS,
    Explanation,
    InsightsService,
    InventoryFilter,
    ProductCategory,
    ProductInsight,
    Window,
    resolve_window,
)
from app.analytics.rto import MIN_RATE_SAMPLE
from app.api.deps import DbSession, EntitlementsDep, TenantPrincipal, require_permission
from app.api.v1.insights_schemas import (
    AgingRowResponse,
    CasePeriodResponse,
    CashInsightsResponse,
    ComparisonResponse,
    CourierCashResponse,
    CourierDiscrepancyResponse,
    CourierInsightsResponse,
    CourierScorecardResponse,
    CustomerInsightsResponse,
    ExplanationResponse,
    ForecastResponse,
    InventoryInsightsResponse,
    KindRowResponse,
    MoneyOverviewResponse,
    MovementRowResponse,
    OverviewResponse,
    PayoutDelayResponse,
    ProductInsightResponse,
    ProductInsightsResponse,
    ReconciliationInsightsResponse,
    StockItemResponse,
    StockOverviewResponse,
    TrendBucketResponse,
    TrendResponse,
    WindowResponse,
)
from app.api.v1.rto_schemas import OutcomeCountsResponse
from app.core.clock import business_date
from app.core.errors import EntitlementRequiredError, ForbiddenError
from app.entitlements.catalog import UNLIMITED, Entitlement
from app.entitlements.service import EntitlementService
from app.money.cashflow import PayoutDelay
from app.reconciliation.models import ItemStatus
from app.tenants.roles import Permission

router = APIRouter(prefix="/analytics/insights", tags=["insights"])

Days = Annotated[int | None, Query(description="7, 30 or 90")]
Since = Annotated[date | None, Query()]
Until = Annotated[date | None, Query()]
SlowDays = Annotated[int, Query(ge=SLOW_MOVING_MIN_DAYS, le=SLOW_MOVING_MAX_DAYS)]
Offset = Annotated[int, Query(ge=0, le=100_000)]
Limit = Annotated[int, Query(ge=1, le=100)]


async def _insights(db: DbSession) -> InsightsService:
    return InsightsService(db)


InsightsDep = Annotated[InsightsService, Depends(_insights)]


async def _money_lock(
    principal: TenantPrincipal,
    entitlements: EntitlementService,
    window: Window,
    *,
    profit: bool,
    advanced: bool = False,
) -> str | None:
    """``None`` when money may be shown for this window, else why not.

    ``profit`` applies the plan's profit-history window, exactly as
    ``/analytics/profit`` enforces it; ``advanced`` adds the per-product profit
    entitlement ``/analytics/products`` requires. Nothing is widened here.
    """
    if not principal.can(Permission.MONEY_VIEW):
        return "PERMISSION"
    tenant = principal.require_tenant()
    if advanced and not await entitlements.is_allowed(tenant, Entitlement.ADVANCED_PROFIT):
        return "PLAN"
    if profit:
        days = await entitlements.limit(tenant, Entitlement.PROFIT_HISTORY_DAYS)
        if days != UNLIMITED:
            earliest = business_date() - timedelta(days=max(0, days - 1))
            if window.since < earliest:
                return "PLAN"
    return None


def _explanations(items: list[Explanation]) -> list[ExplanationResponse]:
    return [ExplanationResponse.of(item) for item in items]


def _delay(delay: PayoutDelay | None) -> PayoutDelayResponse | None:
    if delay is None:
        return None
    return PayoutDelayResponse(
        samples=delay.samples, median_days=delay.median_days, reliable=delay.is_reliable
    )


# --------------------------------------------------------------------------- #
# Overview and trend
# --------------------------------------------------------------------------- #


@router.get(
    "/overview",
    response_model=OverviewResponse,
    summary="Business health for a period, against the one before",
    dependencies=[Depends(require_permission(Permission.ORDER_VIEW))],
)
async def overview(
    principal: TenantPrincipal,
    insights: InsightsDep,
    entitlements: EntitlementsDep,
    days: Days = None,
    since: Since = None,
    until: Until = None,
    slow_days: SlowDays = SLOW_MOVING_DAYS,
) -> OverviewResponse:
    window = resolve_window(days=days, since=since, until=until)
    lock = await _money_lock(principal, entitlements, window, profit=True)
    report = await insights.overview(
        window,
        money=lock is None,
        stock=principal.can(Permission.PRODUCT_VIEW),
        slow_days=slow_days,
    )
    money = report.money
    stock = report.stock
    return OverviewResponse(
        window=WindowResponse.of(report.window),
        previous=WindowResponse.of(report.previous),
        orders=ComparisonResponse.of(report.orders),
        delivered=ComparisonResponse.of(report.delivered),
        rto=OutcomeCountsResponse.of(report.rto),
        rto_previous=OutcomeCountsResponse.of(report.rto_previous),
        rto_trend=report.rto_trend,
        money=None
        if money is None
        else MoneyOverviewResponse(
            revenue=ComparisonResponse.of(money.revenue),
            profit=ComparisonResponse.of(money.profit),
            profit_quality=money.quality,
            received=ComparisonResponse.of(money.received),
            receivable_paisa=money.receivable_paisa,
            overdue_paisa=money.overdue_paisa,
            in_transit_paisa=money.in_transit_paisa,
            open_case_count=money.open_case_count,
            open_case_paisa=money.open_case_paisa,
            discrepancy_count=money.discrepancy_count,
            discrepancy_paisa=money.discrepancy_paisa,
        ),
        money_locked=lock,
        stock=None
        if stock is None
        else StockOverviewResponse(
            tracked_products=stock.summary.tracked_products,
            total_units=stock.summary.total_units,
            low_stock_items=stock.summary.low_stock_items,
            out_of_stock_items=stock.summary.out_of_stock_items,
            slow_moving_items=stock.slow_moving_items,
            slow_moving_days=stock.slow_moving_days,
        ),
        explanations=_explanations(report.explanations),
    )


@router.get(
    "/trend",
    response_model=TrendResponse,
    summary="Orders, parcels, revenue and profit over time",
    dependencies=[Depends(require_permission(Permission.ORDER_VIEW))],
)
async def trend(
    principal: TenantPrincipal,
    insights: InsightsDep,
    entitlements: EntitlementsDep,
    days: Days = None,
    since: Since = None,
    until: Until = None,
) -> TrendResponse:
    window = resolve_window(days=days, since=since, until=until)
    lock = await _money_lock(principal, entitlements, window, profit=True)
    report = await insights.trend(window)
    return TrendResponse(
        window=WindowResponse.of(report.window),
        granularity=report.granularity,
        buckets=[
            TrendBucketResponse(
                start=bucket.start,
                end=bucket.end,
                orders=bucket.orders,
                parcels=bucket.parcels,
                revenue_paisa=bucket.revenue_paisa if lock is None else None,
                profit_paisa=bucket.profit_paisa if lock is None else None,
            )
            for bucket in report.buckets
        ],
        profit_quality=report.quality if lock is None else None,
        money_locked=lock,
    )


# --------------------------------------------------------------------------- #
# Products and couriers
# --------------------------------------------------------------------------- #


def _product(row: ProductInsight, *, money: bool) -> ProductInsightResponse:
    measurable = money and row.measurable_profit
    return ProductInsightResponse(
        product_id=row.product_id,
        name=row.name,
        sku=row.sku,
        parcels=row.parcels,
        units_delivered=row.units_delivered,
        revenue_paisa=row.revenue_paisa if money else None,
        profit_paisa=row.profit_paisa if measurable else None,
        margin_basis_points=row.margin_bps if measurable else None,
        profit_quality=str(row.quality) if money and row.quality is not None else None,
        rto=OutcomeCountsResponse.of(row.rto),
        rto_value_paisa=row.rto_value_paisa,
        stock_status=row.stock_status,
        stock_on_hand=row.stock_on_hand,
        units_booked=row.units_booked,
        last_sale_at=row.last_sale_at,
        slow_moving=row.slow_moving,
        fast_moving=row.fast_moving,
    )


@router.get(
    "/products",
    response_model=ProductInsightsResponse,
    summary="Product performance, with factual categories",
    dependencies=[Depends(require_permission(Permission.PRODUCT_VIEW))],
)
async def products(
    principal: TenantPrincipal,
    insights: InsightsDep,
    entitlements: EntitlementsDep,
    days: Days = None,
    since: Since = None,
    until: Until = None,
    category: Annotated[ProductCategory, Query()] = ProductCategory.ALL,
    slow_days: SlowDays = SLOW_MOVING_DAYS,
    offset: Offset = 0,
    limit: Limit = 20,
) -> ProductInsightsResponse:
    """Revenue and profit columns need money access and the Pro product-profit
    entitlement; stock and RTO do not. A money-ranked category without access
    is refused rather than silently re-sorted by something else."""
    window = resolve_window(days=days, since=since, until=until)
    lock = await _money_lock(principal, entitlements, window, profit=True, advanced=True)
    if category in MONEY_CATEGORIES and lock == "PERMISSION":
        raise ForbiddenError(f"Your role does not allow {Permission.MONEY_VIEW}")
    if category in MONEY_CATEGORIES and lock == "PLAN":
        raise EntitlementRequiredError(
            str(Entitlement.ADVANCED_PROFIT), "Product profit needs a plan that includes it"
        )
    report = await insights.products(
        window,
        category=category,
        money=lock is None,
        slow_days=slow_days,
        offset=offset,
        limit=limit,
    )
    return ProductInsightsResponse(
        window=WindowResponse.of(report.window),
        category=str(report.category),
        slow_moving_days=report.slow_moving_days,
        fast_moving_min_units=FAST_MOVING_MIN_UNITS,
        min_sample=MIN_RATE_SAMPLE,
        counts=report.counts,
        items=[_product(row, money=lock is None) for row in report.page.items],
        total=report.page.total,
        offset=report.page.offset,
        limit=report.page.limit,
        has_more=report.page.has_more,
        money_locked=lock,
    )


@router.get(
    "/couriers",
    response_model=CourierInsightsResponse,
    summary="Courier scorecards — facts side by side, never a ranking",
    dependencies=[Depends(require_permission(Permission.ORDER_VIEW))],
)
async def couriers(
    principal: TenantPrincipal,
    insights: InsightsDep,
    entitlements: EntitlementsDep,
    days: Days = None,
    since: Since = None,
    until: Until = None,
) -> CourierInsightsResponse:
    window = resolve_window(days=days, since=since, until=until)
    lock = await _money_lock(principal, entitlements, window, profit=False)
    report = await insights.couriers(window, money=lock is None)
    items = []
    for card in report.items:
        balance = card.balance
        money = lock is None
        items.append(
            CourierScorecardResponse(
                provider=card.provider,
                counts=OutcomeCountsResponse.of(card.counts),
                recent=OutcomeCountsResponse.of(card.recent),
                previous=OutcomeCountsResponse.of(card.previous),
                trend=card.trend,
                in_transit_now=card.in_transit_now,
                stuck_now=card.stuck_now,
                outstanding_paisa=(balance.outstanding_paisa if balance else 0) if money else None,
                overdue_paisa=(balance.overdue_paisa if balance else 0) if money else None,
                delivered_unpaid_paisa=(balance.delivered_unpaid_paisa if balance else 0)
                if money
                else None,
                in_transit_paisa=(balance.in_transit_paisa if balance else 0) if money else None,
                last_payment_on=balance.last_payment_on if money and balance else None,
                payout_delay=_delay(balance.delay) if money and balance else None,
                discrepancy_count=card.discrepancy_count if money else None,
                discrepancy_paisa=card.discrepancy_paisa if money else None,
            )
        )
    return CourierInsightsResponse(
        window=WindowResponse.of(report.window),
        items=items,
        excluded_providers=report.excluded_providers,
        min_sample=MIN_RATE_SAMPLE,
        money_locked=lock,
    )


# --------------------------------------------------------------------------- #
# Cash and reconciliation — money only
# --------------------------------------------------------------------------- #


@router.get(
    "/cash",
    response_model=CashInsightsResponse,
    summary="Cash received, receivable, overdue and on the road",
    dependencies=[Depends(require_permission(Permission.MONEY_VIEW))],
)
async def cash(
    principal: TenantPrincipal,
    insights: InsightsDep,
    days: Days = None,
    since: Since = None,
    until: Until = None,
) -> CashInsightsResponse:
    window = resolve_window(days=days, since=since, until=until)
    report = await insights.cash(window)
    flow = report.flow
    return CashInsightsResponse(
        window=WindowResponse.of(report.window),
        received=ComparisonResponse.of(report.received),
        received_by_courier=flow.received_by_courier,
        receivable_paisa=flow.receivable_paisa,
        overdue_paisa=flow.overdue_paisa,
        delivered_unpaid_paisa=flow.delivered_unpaid_paisa,
        in_transit_paisa=flow.in_transit_paisa,
        in_transit_count=flow.in_transit_count,
        aging=[
            AgingRowResponse(
                label=row.label,
                min_days=row.min_days,
                max_days=row.max_days,
                count=row.count,
                amount_paisa=row.amount_paisa,
            )
            for row in report.aging
        ],
        couriers=[
            CourierCashResponse(
                provider=row.provider,
                outstanding_paisa=row.outstanding_paisa,
                overdue_paisa=row.overdue_paisa,
                in_transit_paisa=row.in_transit_paisa,
                payout_delay=_delay(row.delay),
            )
            for row in report.couriers
        ],
        forecast=[
            ForecastResponse(key=w.key, parcel_count=w.parcel_count, amount_paisa=w.amount_paisa)
            for w in flow.forecast
        ],
        explanations=_explanations(report.explanations),
    )


@router.get(
    "/reconciliation",
    response_model=ReconciliationInsightsResponse,
    summary="Discrepancies and cases over a period",
    dependencies=[Depends(require_permission(Permission.MONEY_VIEW))],
)
async def reconciliation(
    principal: TenantPrincipal,
    insights: InsightsDep,
    days: Days = None,
    since: Since = None,
    until: Until = None,
) -> ReconciliationInsightsResponse:
    window = resolve_window(days=days, since=since, until=until)
    report = await insights.reconciliation(window)
    summary = report.summary
    counts = summary.counts
    diffs = summary.difference_by_status
    missing, charge = str(ItemStatus.MISSING_COD), str(ItemStatus.CHARGE_MISMATCH)
    return ReconciliationInsightsResponse(
        window=WindowResponse.of(report.window),
        matched=summary.matched,
        discrepancy_count=summary.discrepancies,
        difference_paisa=summary.difference_paisa,
        missing_cod_count=counts.get(missing, 0),
        missing_cod_paisa=abs(diffs.get(missing, 0)),
        charge_mismatch_count=counts.get(charge, 0),
        charge_mismatch_paisa=abs(diffs.get(charge, 0)),
        unmatched_count=summary.unmatched,
        unmatched_paisa=summary.unmatched_paisa,
        open_case_count=summary.open_cases,
        open_case_paisa=summary.open_case_paisa,
        open_by_kind=[
            KindRowResponse(kind=kind, count=count, amount_paisa=amount)
            for kind, count, amount in report.open_by_kind
        ],
        periods=[
            CasePeriodResponse(
                start=period.start, end=period.end, opened=period.opened, resolved=period.resolved
            )
            for period in report.periods
        ],
        by_courier=[
            CourierDiscrepancyResponse(provider=provider, count=count, amount_paisa=amount)
            for provider, count, amount in report.by_courier
        ],
        explanations=_explanations(report.explanations),
    )


# --------------------------------------------------------------------------- #
# Customers and inventory
# --------------------------------------------------------------------------- #


@router.get(
    "/customers",
    response_model=CustomerInsightsResponse,
    summary="New, returning and repeat customers — counts only",
    dependencies=[Depends(require_permission(Permission.CUSTOMER_VIEW))],
)
async def customers(
    principal: TenantPrincipal,
    insights: InsightsDep,
    days: Days = None,
    since: Since = None,
    until: Until = None,
) -> CustomerInsightsResponse:
    window = resolve_window(days=days, since=since, until=until)
    report = await insights.customers(window)
    return CustomerInsightsResponse(
        window=WindowResponse.of(report.window),
        total_customers=report.total_customers,
        active=ComparisonResponse.of(report.active),
        new=ComparisonResponse.of(report.new),
        returning=report.returning,
        orders_with_customer=report.orders_with_customer,
        repeat_orders=report.repeat_orders,
        repeat_order_rate_basis_points=report.repeat_order_rate_bps,
        sufficient=report.sufficient,
        min_sample=MIN_RATE_SAMPLE,
        repeat_customers=report.repeat_customers,
        multi_delivery_customers=report.multi_delivery_customers,
        repeat_rto_customers=report.repeat_rto_customers,
        explanations=_explanations(report.explanations),
    )


@router.get(
    "/inventory",
    response_model=InventoryInsightsResponse,
    summary="Stock health and movement from the stock ledger",
    dependencies=[Depends(require_permission(Permission.PRODUCT_VIEW))],
)
async def inventory(
    principal: TenantPrincipal,
    insights: InsightsDep,
    days: Days = None,
    since: Since = None,
    until: Until = None,
    filter_by: Annotated[InventoryFilter, Query(alias="filter")] = InventoryFilter.ALL,
    slow_days: SlowDays = SLOW_MOVING_DAYS,
    offset: Offset = 0,
    limit: Limit = 20,
) -> InventoryInsightsResponse:
    window = resolve_window(days=days, since=since, until=until)
    report = await insights.inventory(
        window, filter_by=filter_by, slow_days=slow_days, offset=offset, limit=limit
    )
    return InventoryInsightsResponse(
        window=WindowResponse.of(report.window),
        filter=str(filter_by),
        slow_moving_days=report.slow_moving_days,
        fast_moving_min_units=FAST_MOVING_MIN_UNITS,
        tracked_products=report.summary.tracked_products,
        total_units=report.summary.total_units,
        low_stock_items=report.summary.low_stock_items,
        out_of_stock_items=report.summary.out_of_stock_items,
        counts=report.counts,
        movement=[
            MovementRowResponse(reason=reason, units_in=units_in, units_out=units_out)
            for reason, units_in, units_out in report.movement
        ],
        items=[
            StockItemResponse(
                product_id=item.product_id,
                variant_id=item.variant_id,
                name=item.name,
                variant_name=item.variant_name,
                sku=item.sku,
                stock_on_hand=item.stock_on_hand,
                low_stock_threshold=item.low_stock_threshold,
                status=item.status,
                units_booked=item.units_booked,
                last_sale_at=item.last_sale_at,
                slow_moving=item.slow_moving,
                fast_moving=item.fast_moving,
            )
            for item in report.page.items
        ],
        total=report.page.total,
        offset=report.page.offset,
        limit=report.page.limit,
        has_more=report.page.has_more,
        explanations=_explanations(report.explanations),
    )
