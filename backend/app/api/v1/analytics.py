"""Analytics, expenses and the notification centre (master spec section 39).

``GET /v1/analytics/home`` is the screen a seller opens first every morning,
so it is one round trip: the day's counts, the money still owed, and the four
alerts, together. Making the phone stitch that from four calls would mean four
chances for one to fail and leave a half-drawn control screen.

Nothing here maintains an aggregate table. Every figure is computed from
current profit snapshots and live receivables when it is asked for, which is
what keeps the dashboard reconciled to the ledger (section 81.10). If that
ever becomes too slow, the fix is a materialised view with a freshness stamp
on the wire — not a counter that silently drifts.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.analytics.service import (
    AnalyticsService,
    ProductLine,
    RateLine,
    default_window,
)
from app.api.deps import DbSession, EntitlementsDep, TenantPrincipal, require_permission
from app.api.v1.analytics_schemas import (
    AlertResponse,
    AllocationRequest,
    AllocationResponse,
    DayPointResponse,
    ExpenseCreatePayload,
    ExpenseResponse,
    FunnelStageResponse,
    HomeResponse,
    NotificationResponse,
    ProductLineResponse,
    ProfitResponse,
    RateLineResponse,
    ReturnsResponse,
    UnreadCountResponse,
    WeeklySummaryResponse,
)
from app.common.pagination import Page, decode_cursor
from app.core.clock import business_date
from app.core.errors import EntitlementRequiredError
from app.entitlements.catalog import UNLIMITED, Entitlement
from app.entitlements.service import EntitlementService
from app.expenses.models import ALLOCATION_VERSION, Expense, ExpenseKind
from app.expenses.service import ExpenseService
from app.notifications.alerts import AlertService, AlertSummary
from app.notifications.models import Severity
from app.notifications.service import NotificationService
from app.tenants.roles import Permission

router = APIRouter(tags=["analytics"])


async def _analytics(db: DbSession) -> AnalyticsService:
    return AnalyticsService(db)


async def _expenses(db: DbSession) -> ExpenseService:
    return ExpenseService(db)


async def _notifications(db: DbSession) -> NotificationService:
    return NotificationService(db)


async def _alerts(db: DbSession) -> AlertService:
    return AlertService(db)


AnalyticsDep = Annotated[AnalyticsService, Depends(_analytics)]
ExpensesDep = Annotated[ExpenseService, Depends(_expenses)]
NotificationsDep = Annotated[NotificationService, Depends(_notifications)]
AlertsDep = Annotated[AlertService, Depends(_alerts)]


def _window(since: date | None, until: date | None) -> tuple[date, date]:
    """Resolve the requested period, defaulting to the last 30 days."""
    default_since, default_until = default_window()
    return since or default_since, until or default_until


async def _require_history_window(
    entitlements: EntitlementService, tenant_id: uuid.UUID, since: date
) -> None:
    """Section 26's profit-history window: today, full, or advanced.

    Refuses rather than silently clamping. A screen that quietly narrowed the
    range would show a smaller profit figure than the seller asked for, with
    nothing on it saying so — which section 135 treats as the same failure as
    rendering an estimate as exact. The allowed window is returned in the error
    so the client can offer the range it *can* have.
    """
    days = await entitlements.limit(tenant_id, Entitlement.PROFIT_HISTORY_DAYS)
    if days == UNLIMITED:
        return
    earliest = business_date() - timedelta(days=max(0, days - 1))
    if since >= earliest:
        return
    raise EntitlementRequiredError(
        str(Entitlement.PROFIT_HISTORY_DAYS),
        f"Your plan includes {days} day(s) of profit history",
        details={"max_days": str(days), "earliest_business_date": earliest.isoformat()},
    )


def _alert(summary: AlertSummary) -> AlertResponse:
    return AlertResponse(
        kind=summary.kind,
        severity=summary.severity,
        count=summary.count,
        amount_paisa=summary.amount_paisa,
    )


def _rate_line(line: RateLine) -> RateLineResponse:
    return RateLineResponse(
        label=line.label,
        parcel_count=line.parcel_count,
        return_count=line.return_count,
        return_rate_basis_points=line.return_rate_basis_points,
        loss_paisa=line.loss_paisa,
        has_enough_sample=line.has_enough_sample,
    )


def _product_line(line: ProductLine) -> ProductLineResponse:
    return ProductLineResponse(
        product_name=line.product_name,
        parcel_count=line.parcel_count,
        units_delivered=line.units_delivered,
        revenue_paisa=line.revenue_paisa,
        profit_paisa=line.profit_paisa,
        return_count=line.return_count,
        margin_basis_points=line.margin_basis_points,
        has_enough_sample=line.has_enough_sample,
    )


def _expense(expense: Expense) -> ExpenseResponse:
    return ExpenseResponse(
        id=expense.id,
        kind=expense.kind,
        amount_paisa=expense.amount_paisa,
        allocated_paisa=expense.allocated_paisa,
        unallocated_paisa=expense.unallocated_paisa,
        period_start=expense.period_start,
        period_end=expense.period_end,
        description=expense.description,
        product_id=expense.product_id,
        preferred_method=expense.preferred_method,
        allocated_at=expense.allocated_at,
        created_at=expense.created_at,
    )


# --------------------------------------------------------------------------- #
# Analytics
# --------------------------------------------------------------------------- #


@router.get(
    "/analytics/home",
    response_model=HomeResponse,
    summary="The daily money control screen",
    dependencies=[Depends(require_permission(Permission.MONEY_VIEW))],
)
async def home(
    principal: TenantPrincipal,
    analytics: AnalyticsDep,
    as_of: Annotated[date | None, Query()] = None,
) -> HomeResponse:
    """Section 1.1's metrics for one Asia/Dhaka business date.

    ``as_of`` is the business date, not a UTC day. A seller looking at this at
    01:00 Dhaka is looking at today, and the response says which date it means
    rather than leaving the phone to guess from its own clock.
    """
    metrics = await analytics.home(as_of=as_of)
    return HomeResponse(
        as_of=metrics.as_of,
        orders_today=metrics.orders_today,
        delivered_today=metrics.delivered_today,
        returned_today=metrics.returned_today,
        gross_sales_paisa=metrics.gross_sales_paisa,
        realized_revenue_paisa=metrics.realized_revenue_paisa,
        contribution_profit_paisa=metrics.contribution_profit_paisa,
        cod_outstanding_paisa=metrics.cod_outstanding_paisa,
        cod_expected_today_paisa=metrics.cod_expected_today_paisa,
        cod_unforecast_paisa=metrics.cod_unforecast_paisa,
        cod_overdue_paisa=metrics.cod_overdue_paisa,
        mismatch_paisa=metrics.mismatch_paisa,
        mismatch_count=metrics.mismatch_count,
        return_loss_paisa=metrics.return_loss_paisa,
        estimated_parcels=metrics.estimated_parcels,
        incomplete_parcels=metrics.incomplete_parcels,
        alerts=[_alert(alert) for alert in metrics.alerts],
    )


@router.get(
    "/analytics/profit",
    response_model=ProfitResponse,
    summary="P&L for a period",
    dependencies=[Depends(require_permission(Permission.MONEY_VIEW))],
)
async def profit(
    principal: TenantPrincipal,
    analytics: AnalyticsDep,
    entitlements: EntitlementsDep,
    since: Annotated[date | None, Query()] = None,
    until: Annotated[date | None, Query()] = None,
) -> ProfitResponse:
    """Contribution profit, its parts, and how much of it is measured.

    ``quality`` is not decoration. A ৳40,000 profit built from twelve
    estimated parcels and a ৳40,000 profit built from twelve settled ones are
    different claims, and section 135 requires the screen to be able to tell
    them apart.
    """
    start, end = _window(since, until)
    await _require_history_window(entitlements, principal.require_tenant(), start)
    report = await analytics.profit(since=start, until=end)
    return ProfitResponse(
        since=report.since,
        until=report.until,
        parcel_count=report.parcel_count,
        realized_revenue_paisa=report.realized_revenue_paisa,
        item_cost_paisa=report.item_cost_paisa,
        delivery_charge_paisa=report.delivery_charge_paisa,
        cod_fee_paisa=report.cod_fee_paisa,
        return_charge_paisa=report.return_charge_paisa,
        packaging_paisa=report.packaging_paisa,
        ad_cost_paisa=report.ad_cost_paisa,
        write_off_cost_paisa=report.write_off_cost_paisa,
        contribution_profit_paisa=report.contribution_profit_paisa,
        unallocated_ad_spend_paisa=report.unallocated_ad_spend_paisa,
        fixed_cost_paisa=report.fixed_cost_paisa,
        operating_profit_paisa=report.operating_profit_paisa,
        margin_basis_points=report.margin_basis_points,
        quality={str(quality): count for quality, count in report.quality.items()},
        series=[
            DayPointResponse(
                business_date=point.business_date,
                parcel_count=point.parcel_count,
                realized_revenue_paisa=point.realized_revenue_paisa,
                contribution_profit_paisa=point.contribution_profit_paisa,
            )
            for point in report.series
        ],
        funnel=[
            FunnelStageResponse(label=stage.label, count=stage.count) for stage in report.funnel
        ],
    )


@router.get(
    "/analytics/returns",
    response_model=ReturnsResponse,
    summary="Return economics",
    dependencies=[Depends(require_permission(Permission.MONEY_VIEW))],
)
async def returns(
    principal: TenantPrincipal,
    analytics: AnalyticsDep,
    entitlements: EntitlementsDep,
    since: Annotated[date | None, Query()] = None,
    until: Annotated[date | None, Query()] = None,
) -> ReturnsResponse:
    """Section 19's list: what returns cost, and where they come from.

    The three breakdowns are ordered worst-rate first, because a return report
    is opened to find the problem rather than to browse.
    """
    await entitlements.require(principal.require_tenant(), Entitlement.ADVANCED_PROFIT)
    start, end = _window(since, until)
    report = await analytics.returns(since=start, until=end)
    return ReturnsResponse(
        since=report.since,
        until=report.until,
        parcel_count=report.parcel_count,
        return_count=report.return_count,
        return_rate_basis_points=report.return_rate_basis_points,
        direct_loss_paisa=report.direct_loss_paisa,
        outward_delivery_cost_paisa=report.outward_delivery_cost_paisa,
        return_delivery_cost_paisa=report.return_delivery_cost_paisa,
        packaging_loss_paisa=report.packaging_loss_paisa,
        write_off_paisa=report.write_off_paisa,
        by_reason=report.by_reason,
        unknown_reason_count=report.unknown_reason_count,
        by_product=[_rate_line(line) for line in report.by_product],
        by_area=[_rate_line(line) for line in report.by_area],
        by_courier=[_rate_line(line) for line in report.by_courier],
    )


@router.get(
    "/analytics/products",
    response_model=list[ProductLineResponse],
    summary="Contribution profit by product",
    dependencies=[Depends(require_permission(Permission.MONEY_VIEW))],
)
async def products(
    principal: TenantPrincipal,
    analytics: AnalyticsDep,
    entitlements: EntitlementsDep,
    since: Annotated[date | None, Query()] = None,
    until: Annotated[date | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ProductLineResponse]:
    """Best first.

    Rows below the sample threshold are returned with ``has_enough_sample:
    false`` rather than dropped: a seller who sold two of something should see
    those two, greyed, instead of wondering why the product vanished.
    """
    await entitlements.require(principal.require_tenant(), Entitlement.ADVANCED_PROFIT)
    start, end = _window(since, until)
    lines = await analytics.products(since=start, until=end, limit=limit)
    return [_product_line(line) for line in lines]


# --------------------------------------------------------------------------- #
# Expenses
# --------------------------------------------------------------------------- #


@router.post(
    "/expenses",
    response_model=ExpenseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record money spent",
    dependencies=[Depends(require_permission(Permission.MONEY_RECONCILE))],
)
async def create_expense(
    payload: ExpenseCreatePayload,
    principal: TenantPrincipal,
    expenses: ExpensesDep,
    db: DbSession,
) -> ExpenseResponse:
    """Recording is not allocating.

    This changes no profit figure. Section 86: an expense reaches orders only
    through an explicit allocation, so the seller always knows which number
    moved and why.
    """
    expense = await expenses.record(
        kind=payload.kind,
        amount_paisa=payload.amount_paisa,
        period_start=payload.period_start,
        period_end=payload.period_end,
        description=payload.description,
        product_id=payload.product_id,
        preferred_method=payload.preferred_method,
    )
    await db.commit()
    return _expense(expense)


@router.get(
    "/expenses",
    response_model=Page[ExpenseResponse],
    summary="List expenses",
    dependencies=[Depends(require_permission(Permission.MONEY_VIEW))],
)
async def list_expenses(
    principal: TenantPrincipal,
    expenses: ExpensesDep,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    kind: Annotated[ExpenseKind | None, Query()] = None,
    since: Annotated[date | None, Query()] = None,
    until: Annotated[date | None, Query()] = None,
) -> Page[ExpenseResponse]:
    rows = await expenses.list_expenses(
        limit=limit,
        cursor=decode_cursor(cursor) if cursor else None,
        kind=kind,
        since=since,
        until=until,
    )
    return Page[ExpenseResponse].build(rows, limit=limit, serializer=_expense)


@router.get(
    "/expenses/{expense_id}",
    response_model=ExpenseResponse,
    summary="One expense",
    dependencies=[Depends(require_permission(Permission.MONEY_VIEW))],
)
async def get_expense(
    expense_id: uuid.UUID,
    principal: TenantPrincipal,
    expenses: ExpensesDep,
) -> ExpenseResponse:
    return _expense(await expenses.get(expense_id))


@router.post(
    "/expenses/{expense_id}/allocate",
    response_model=AllocationResponse,
    summary="Spread an expense over its parcels",
    dependencies=[Depends(require_permission(Permission.MONEY_RECONCILE))],
)
async def allocate_expense(
    expense_id: uuid.UUID,
    payload: AllocationRequest,
    principal: TenantPrincipal,
    expenses: ExpensesDep,
    db: DbSession,
) -> AllocationResponse:
    """Write each parcel's share and a new profit revision for it.

    Re-allocating requires a reason, because it rewrites profit figures the
    seller has already read. The previous revisions stay readable.
    """
    report = await expenses.allocate(expense_id, method=payload.method, reason=payload.reason)
    await db.commit()
    return AllocationResponse(
        expense_id=report.expense_id,
        method=str(report.method),
        version=ALLOCATION_VERSION,
        parcel_count=report.parcel_count,
        allocated_paisa=report.allocated_paisa,
        unallocated_paisa=report.unallocated_paisa,
        reached_nothing=report.reached_nothing,
    )


# --------------------------------------------------------------------------- #
# Notification centre
# --------------------------------------------------------------------------- #


@router.get(
    "/notifications",
    response_model=Page[NotificationResponse],
    summary="The notification centre",
)
async def list_notifications(
    principal: TenantPrincipal,
    notifications: NotificationsDep,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    unread_only: Annotated[bool, Query()] = False,
    severity: Annotated[Severity | None, Query()] = None,
) -> Page[NotificationResponse]:
    """Section 94: push is not enough, so everything is also here."""
    rows = await notifications.list_notifications(
        limit=limit,
        cursor=decode_cursor(cursor) if cursor else None,
        unread_only=unread_only,
        severity=severity,
    )
    return Page[NotificationResponse].build(
        rows, limit=limit, serializer=NotificationResponse.model_validate
    )


@router.get(
    "/notifications/unread-count",
    response_model=UnreadCountResponse,
    summary="Badge count",
)
async def unread_count(
    principal: TenantPrincipal,
    notifications: NotificationsDep,
) -> UnreadCountResponse:
    return UnreadCountResponse(unread=await notifications.unread_count())


@router.post(
    "/notifications/{notification_id}/read",
    response_model=NotificationResponse,
    summary="Mark one read",
)
async def mark_read(
    notification_id: uuid.UUID,
    principal: TenantPrincipal,
    notifications: NotificationsDep,
    db: DbSession,
) -> NotificationResponse:
    notification = await notifications.mark_read(notification_id)
    await db.commit()
    return NotificationResponse.model_validate(notification)


@router.post(
    "/notifications/read-all",
    response_model=UnreadCountResponse,
    summary="Clear the badge",
)
async def mark_all_read(
    principal: TenantPrincipal,
    notifications: NotificationsDep,
    db: DbSession,
) -> UnreadCountResponse:
    """Returns the number still unread, which after this is zero.

    Phrased as the remaining count rather than "ok" so a client that raced
    another device sees the truth instead of assuming it won.
    """
    await notifications.mark_all_read()
    await db.commit()
    return UnreadCountResponse(unread=await notifications.unread_count())


@router.get(
    "/analytics/weekly-summary",
    response_model=WeeklySummaryResponse,
    summary="The Friday figures",
    dependencies=[Depends(require_permission(Permission.MONEY_VIEW))],
)
async def weekly_summary(
    principal: TenantPrincipal,
    alerts: AlertsDep,
    week_end: Annotated[date | None, Query()] = None,
) -> WeeklySummaryResponse:
    """Computed on demand, so the screen works before Friday has come round.

    The scheduled job writes the same figures into a notification; this is the
    same calculation, which is why the two never disagree.
    """
    summary = await alerts.weekly_summary(week_end=week_end)
    return WeeklySummaryResponse(
        week_start=summary.week_start,
        week_end=summary.week_end,
        order_count=summary.order_count,
        delivered_count=summary.delivered_count,
        return_count=summary.return_count,
        return_loss_paisa=summary.return_loss_paisa,
        sales_paisa=summary.sales_paisa,
        contribution_profit_paisa=summary.contribution_profit_paisa,
        cod_outstanding_paisa=summary.cod_outstanding_paisa,
        overdue_paisa=summary.overdue_paisa,
        mismatch_count=summary.mismatch_count,
        ad_spend_paisa=summary.ad_spend_paisa,
        return_rate_basis_points=summary.return_rate_basis_points,
        best_product=summary.best_product,
        worst_product=summary.worst_product,
        best_courier=summary.best_courier,
        worst_courier=summary.worst_courier,
        ranking_note=summary.ranking_note,
        courier_note=summary.courier_note,
        alerts=[_alert(alert) for alert in summary.alerts],
    )
