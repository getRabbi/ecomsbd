"""Wire shapes for return / RTO intelligence.

Counts are integers and rates are basis points, like the rest of analytics.
Every rate travels with the counts it was computed from and a ``sufficient``
flag, so a client can always say "3 of 5 completed parcels" instead of a bare
percentage, and can label a thin sample as limited data.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel

from app.analytics.rto import (
    AreaReport,
    CourierRto,
    CustomerHistory,
    CustomerPattern,
    Observation,
    OutcomeCounts,
    ParcelEvent,
    ProductRto,
)

__all__ = [
    "AreaReportResponse",
    "CourierReportResponse",
    "CustomerHistoryResponse",
    "OutcomeCountsResponse",
    "PatternReportResponse",
    "ProductPageResponse",
    "RtoSummaryResponse",
    "RtoTrendResponse",
]


class OutcomeCountsResponse(BaseModel):
    #: The RTO denominator: delivered + partial + rto.
    completed: int
    delivered: int
    partial: int
    #: returned + courier_cancelled.
    rto: int
    returned: int
    courier_cancelled: int
    #: Lost or damaged by the courier; outside the denominator.
    lost: int
    rto_rate_basis_points: int | None
    success_rate_basis_points: int | None
    #: ``completed`` reached the minimum sample for comparisons.
    sufficient: bool

    @classmethod
    def of(cls, counts: OutcomeCounts) -> OutcomeCountsResponse:
        return cls(
            completed=counts.completed,
            delivered=counts.delivered,
            partial=counts.partial,
            rto=counts.rto,
            returned=counts.returned,
            courier_cancelled=counts.courier_cancelled,
            lost=counts.lost,
            rto_rate_basis_points=counts.rto_rate_bps,
            success_rate_basis_points=counts.success_rate_bps,
            sufficient=counts.sufficient,
        )


class RtoDefinitionResponse(BaseModel):
    """The classification the numbers were computed with, machine-readable."""

    rto_statuses: list[str]
    completed_statuses: list[str]
    open_statuses: list[str]
    min_sample: int


class WindowResponse(BaseModel):
    days: int
    since: date
    counts: OutcomeCountsResponse


class RtoSummaryResponse(BaseModel):
    since: date
    until: date
    days: int
    counts: OutcomeCountsResponse
    open_now: int
    cancelled_before_dispatch: int
    windows: list[WindowResponse]
    definition: RtoDefinitionResponse


class TrendPointResponse(BaseModel):
    week_start: date
    week_end: date
    counts: OutcomeCountsResponse


class RtoTrendResponse(BaseModel):
    weeks: int
    min_sample: int
    points: list[TrendPointResponse]


class ProductRtoResponse(BaseModel):
    product_id: uuid.UUID | None
    product_name: str
    counts: OutcomeCountsResponse
    rto_value_paisa: int

    @classmethod
    def of(cls, row: ProductRto) -> ProductRtoResponse:
        return cls(
            product_id=row.product_id,
            product_name=row.product_name,
            counts=OutcomeCountsResponse.of(row.counts),
            rto_value_paisa=row.rto_value_paisa,
        )


class ProductPageResponse(BaseModel):
    since: date
    until: date
    days: int
    min_sample: int
    items: list[ProductRtoResponse]
    total: int
    limit: int
    offset: int
    has_more: bool


class CourierRtoResponse(BaseModel):
    provider: str
    counts: OutcomeCountsResponse
    #: Last 30 days, and the 30 days before that, for the trend.
    recent: OutcomeCountsResponse
    previous: OutcomeCountsResponse
    #: UP / DOWN / FLAT / INSUFFICIENT_DATA — RTO rate, recent vs previous.
    trend: str
    in_transit_now: int

    @classmethod
    def of(cls, row: CourierRto) -> CourierRtoResponse:
        return cls(
            provider=row.provider,
            counts=OutcomeCountsResponse.of(row.counts),
            recent=OutcomeCountsResponse.of(row.recent),
            previous=OutcomeCountsResponse.of(row.previous),
            trend=row.trend,
            in_transit_now=row.in_transit_now,
        )


class CourierReportResponse(BaseModel):
    since: date
    until: date
    days: int
    min_sample: int
    items: list[CourierRtoResponse]
    #: Providers whose performance is not reported (no live integration).
    excluded_providers: list[str]


class AreaRowResponse(BaseModel):
    label: str
    counts: OutcomeCountsResponse


class AreaReportResponse(BaseModel):
    since: date
    until: date
    days: int
    #: ACTIVE, or DATA_NOT_RELIABLE with no rows.
    status: str
    level: str
    completed: int
    with_area: int
    coverage_basis_points: int | None
    min_coverage_basis_points: int
    min_sample: int
    items: list[AreaRowResponse]
    total: int
    limit: int
    offset: int
    has_more: bool

    @staticmethod
    def rows_of(report: AreaReport) -> list[AreaRowResponse]:
        return [
            AreaRowResponse(label=row.label, counts=OutcomeCountsResponse.of(row.counts))
            for row in report.rows
        ]


class ObservationResponse(BaseModel):
    """A factual pattern. ``code`` is localised by the client from the counts."""

    code: str
    parcel_count: int
    rto_count: int
    delivered_count: int
    earlier_parcel_count: int
    earlier_rto_count: int
    product_name: str | None

    @classmethod
    def of(cls, obs: Observation) -> ObservationResponse:
        return cls(
            code=str(obs.code),
            parcel_count=obs.parcel_count,
            rto_count=obs.rto_count,
            delivered_count=obs.delivered_count,
            earlier_parcel_count=obs.earlier_parcel_count,
            earlier_rto_count=obs.earlier_rto_count,
            product_name=obs.product_name,
        )


class ParcelEventResponse(BaseModel):
    order_number: str
    #: DELIVERED / PARTIAL / RTO / LOST / IN_TRANSIT.
    outcome: str
    status: str
    provider: str
    at: datetime | None

    @classmethod
    def of(cls, event: ParcelEvent) -> ParcelEventResponse:
        return cls(
            order_number=event.order_number,
            outcome=str(event.outcome),
            status=event.status,
            provider=event.provider,
            at=event.at,
        )


class CustomerPatternResponse(BaseModel):
    customer_id: uuid.UUID
    name: str | None
    phone_masked: str | None
    counts: OutcomeCountsResponse
    observations: list[ObservationResponse]

    @classmethod
    def of(cls, row: CustomerPattern) -> CustomerPatternResponse:
        return cls(
            customer_id=row.customer_id,
            name=row.name,
            phone_masked=row.phone_masked,
            counts=OutcomeCountsResponse.of(row.counts),
            observations=[ObservationResponse.of(obs) for obs in row.observations],
        )


class PatternReportResponse(BaseModel):
    since: date
    until: date
    days: int
    items: list[CustomerPatternResponse]


class CustomerHistoryResponse(BaseModel):
    """One customer's parcels with this shop, and the V1 band they imply."""

    customer_id: uuid.UUID
    name: str | None
    phone_masked: str | None
    order_count: int
    counts: OutcomeCountsResponse
    in_transit_count: int
    cancelled_before_dispatch: int
    first_order_at: datetime | None
    last_order_at: datetime | None
    recent: list[ParcelEventResponse]
    observations: list[ObservationResponse]
    risk_state: str
    risk_reasons: list[str]

    @staticmethod
    def parts(history: CustomerHistory) -> dict[str, object]:
        return {
            "order_count": history.order_count,
            "counts": OutcomeCountsResponse.of(history.counts),
            "in_transit_count": history.counts.in_transit,
            "cancelled_before_dispatch": history.cancelled_count,
            "first_order_at": history.first_order_at,
            "last_order_at": history.last_order_at,
            "recent": [ParcelEventResponse.of(event) for event in history.recent],
            "observations": [ObservationResponse.of(obs) for obs in history.observations],
        }
