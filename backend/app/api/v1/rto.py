"""Return / RTO intelligence endpoints.

Shop-level aggregates need ``order.view``: they are counts of the shop's own
parcels and carry no customer identity. Anything naming a customer — repeat
patterns and one customer's history — needs ``customer.risk_view``, the same
permission as the V1 Risk Check it extends.

Web and mobile read these same endpoints; neither computes a rate itself.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query

from app.analytics.rto import (
    AREA_MIN_COVERAGE_BPS,
    COMPLETED_STATUSES,
    MIN_RATE_SAMPLE,
    NOT_LIVE_PROVIDERS,
    ParcelOutcome,
    ProductRto,
    RtoService,
    statuses_for,
)
from app.api.deps import DbSession, TenantPrincipal, require_permission
from app.api.v1.rto_schemas import (
    AreaReportResponse,
    CourierReportResponse,
    CourierRtoResponse,
    CustomerHistoryResponse,
    CustomerPatternResponse,
    OutcomeCountsResponse,
    PatternReportResponse,
    ProductPageResponse,
    ProductRtoResponse,
    RtoDefinitionResponse,
    RtoSummaryResponse,
    RtoTrendResponse,
    TrendPointResponse,
    WindowResponse,
)
from app.core.errors import NotFoundError
from app.customers import risk as risk_rules
from app.customers.models import Customer
from app.tenants.roles import Permission

router = APIRouter(prefix="/analytics/rto", tags=["rto"])

Days = Annotated[int, Query(ge=7, le=365)]
ProductSort = Literal["rto_rate", "rto_count", "completed", "rto_value"]


async def _rto(db: DbSession) -> RtoService:
    return RtoService(db)


RtoDep = Annotated[RtoService, Depends(_rto)]


def _definition() -> RtoDefinitionResponse:
    return RtoDefinitionResponse(
        rto_statuses=statuses_for(ParcelOutcome.RTO),
        completed_statuses=list(COMPLETED_STATUSES),
        open_statuses=statuses_for(ParcelOutcome.IN_TRANSIT),
        min_sample=MIN_RATE_SAMPLE,
    )


@router.get(
    "/summary",
    response_model=RtoSummaryResponse,
    summary="RTO rate, counts and 7/30/90-day windows",
    dependencies=[Depends(require_permission(Permission.ORDER_VIEW))],
)
async def summary(principal: TenantPrincipal, rto: RtoDep, days: Days = 30) -> RtoSummaryResponse:
    report = await rto.summary(days=days)
    return RtoSummaryResponse(
        since=report.since,
        until=report.until,
        days=report.days,
        counts=OutcomeCountsResponse.of(report.counts),
        open_now=report.open_now,
        cancelled_before_dispatch=report.cancelled_before_dispatch,
        windows=[
            WindowResponse(
                days=window.days,
                since=window.since,
                counts=OutcomeCountsResponse.of(window.counts),
            )
            for window in report.windows
        ],
        definition=_definition(),
    )


@router.get(
    "/trend",
    response_model=RtoTrendResponse,
    summary="Weekly RTO, oldest first",
    dependencies=[Depends(require_permission(Permission.ORDER_VIEW))],
)
async def trend(
    principal: TenantPrincipal,
    rto: RtoDep,
    weeks: Annotated[int, Query(ge=4, le=26)] = 12,
) -> RtoTrendResponse:
    points = await rto.trend(weeks=weeks)
    return RtoTrendResponse(
        weeks=weeks,
        min_sample=MIN_RATE_SAMPLE,
        points=[
            TrendPointResponse(
                week_start=point.week_start,
                week_end=point.week_end,
                counts=OutcomeCountsResponse.of(point.counts),
            )
            for point in points
        ],
    )


def _product_sort_key(sort: ProductSort):  # type: ignore[no-untyped-def]
    def key(row: ProductRto) -> tuple[object, ...]:
        counts = row.counts
        match sort:
            case "rto_count":
                return (-counts.rto, -counts.completed, row.product_name)
            case "completed":
                return (-counts.completed, -counts.rto, row.product_name)
            case "rto_value":
                return (-row.rto_value_paisa, -counts.rto, row.product_name)
            case _:
                # Limited-sample rows never outrank measured ones: 1 of 1
                # returned is not a worse product than 30 of 100.
                return (
                    not counts.sufficient,
                    -(counts.rto_rate_bps or 0),
                    -counts.rto,
                    row.product_name,
                )

    return key


@router.get(
    "/products",
    response_model=ProductPageResponse,
    summary="RTO by product, paginated",
    dependencies=[Depends(require_permission(Permission.ORDER_VIEW))],
)
async def products(
    principal: TenantPrincipal,
    rto: RtoDep,
    days: Days = 90,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
    search: Annotated[str | None, Query(max_length=120)] = None,
    sort: ProductSort = "rto_rate",
    min_completed: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> ProductPageResponse:
    since, until, _ = rto.window(days)
    rows = await rto.products(days=days)
    needle = (search or "").strip().casefold()
    if needle:
        rows = [row for row in rows if needle in row.product_name.casefold()]
    if min_completed:
        rows = [row for row in rows if row.counts.completed >= min_completed]
    rows.sort(key=_product_sort_key(sort))
    page = rows[offset : offset + limit]
    return ProductPageResponse(
        since=since,
        until=until,
        days=days,
        min_sample=MIN_RATE_SAMPLE,
        items=[ProductRtoResponse.of(row) for row in page],
        total=len(rows),
        limit=limit,
        offset=offset,
        has_more=offset + limit < len(rows),
    )


@router.get(
    "/couriers",
    response_model=CourierReportResponse,
    summary="RTO by courier",
    dependencies=[Depends(require_permission(Permission.ORDER_VIEW))],
)
async def couriers(
    principal: TenantPrincipal, rto: RtoDep, days: Days = 90
) -> CourierReportResponse:
    since, until, _ = rto.window(days)
    rows = await rto.couriers(days=days)
    return CourierReportResponse(
        since=since,
        until=until,
        days=days,
        min_sample=MIN_RATE_SAMPLE,
        items=[CourierRtoResponse.of(row) for row in rows],
        excluded_providers=sorted(NOT_LIVE_PROVIDERS),
    )


@router.get(
    "/areas",
    response_model=AreaReportResponse,
    summary="RTO by district, when district data is reliable",
    dependencies=[Depends(require_permission(Permission.ORDER_VIEW))],
)
async def areas(
    principal: TenantPrincipal,
    rto: RtoDep,
    days: Days = 90,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> AreaReportResponse:
    since, until, _ = rto.window(days)
    report = await rto.areas(days=days)
    report.rows.sort(
        key=lambda row: (
            not row.counts.sufficient,
            -(row.counts.rto_rate_bps or 0),
            -row.counts.completed,
            row.label,
        )
    )
    total = len(report.rows)
    report.rows = report.rows[offset : offset + limit]
    return AreaReportResponse(
        since=since,
        until=until,
        days=days,
        status=report.status,
        level=report.level,
        completed=report.completed,
        with_area=report.with_area,
        coverage_basis_points=report.coverage_bps,
        min_coverage_basis_points=AREA_MIN_COVERAGE_BPS,
        min_sample=MIN_RATE_SAMPLE,
        items=AreaReportResponse.rows_of(report),
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + limit < total,
    )


@router.get(
    "/patterns",
    response_model=PatternReportResponse,
    summary="Customers whose parcels show a repeat return pattern",
    dependencies=[Depends(require_permission(Permission.CUSTOMER_RISK_VIEW))],
)
async def patterns(
    principal: TenantPrincipal,
    rto: RtoDep,
    days: Days = 90,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> PatternReportResponse:
    report = await rto.patterns(days=days, limit=limit)
    return PatternReportResponse(
        since=report.since,
        until=report.until,
        days=report.days,
        items=[CustomerPatternResponse.of(row) for row in report.customers],
    )


@router.get(
    "/customers/{customer_id}",
    response_model=CustomerHistoryResponse,
    summary="One customer's parcel history and V1 risk band",
    dependencies=[Depends(require_permission(Permission.CUSTOMER_RISK_VIEW))],
)
async def customer_history(
    customer_id: uuid.UUID, principal: TenantPrincipal, db: DbSession, rto: RtoDep
) -> CustomerHistoryResponse:
    # Tenant-scoped by the ORM guard: another shop's customer id finds nothing.
    customer = (
        await db.execute(sa.select(Customer).where(Customer.id == customer_id))
    ).scalar_one_or_none()
    if customer is None:
        raise NotFoundError("Customer not found")
    history = await rto.customer_history(customer.id)
    assessment = risk_rules.assess(history)
    return CustomerHistoryResponse(
        customer_id=customer.id,
        name=customer.name,
        phone_masked=customer.phone_masked,
        risk_state=str(assessment.state),
        risk_reasons=[str(reason) for reason in assessment.reasons],
        **CustomerHistoryResponse.parts(history),
    )


__all__ = ["router"]
