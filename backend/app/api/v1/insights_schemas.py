"""Wire shapes for Advanced Insights.

Money is integer paisa, rates and changes are basis points, like the rest of
analytics. A figure the caller may not see, or that cannot be known, is
``null`` — never a zero standing in for "unknown". ``money_locked`` says why
money is missing: ``PERMISSION`` (the role cannot see money) or ``PLAN``
(the range is beyond the plan's profit history).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel

from app.analytics.insights import Comparison, Explanation, Window
from app.api.v1.rto_schemas import OutcomeCountsResponse

__all__ = [
    "CashInsightsResponse",
    "ComparisonResponse",
    "CourierInsightsResponse",
    "CustomerInsightsResponse",
    "ExplanationResponse",
    "InventoryInsightsResponse",
    "OverviewResponse",
    "ProductInsightsResponse",
    "ReconciliationInsightsResponse",
    "TrendResponse",
    "WindowResponse",
]


class WindowResponse(BaseModel):
    since: date
    until: date
    days: int

    @classmethod
    def of(cls, window: Window) -> WindowResponse:
        return cls(since=window.since, until=window.until, days=window.days)


class ComparisonResponse(BaseModel):
    current: int
    previous: int
    #: ``null`` when the earlier base is too small for a percentage to mean anything.
    change_basis_points: int | None

    @classmethod
    def of(cls, comparison: Comparison) -> ComparisonResponse:
        return cls(
            current=comparison.current,
            previous=comparison.previous,
            change_basis_points=comparison.change_bps,
        )


class ExplanationResponse(BaseModel):
    """A deterministic fact. Clients word it from ``code`` and ``params``."""

    code: str
    params: dict[str, int | str | None]

    @classmethod
    def of(cls, explanation: Explanation) -> ExplanationResponse:
        return cls(code=str(explanation.code), params=dict(explanation.params))


class MoneyOverviewResponse(BaseModel):
    revenue: ComparisonResponse
    #: Contribution profit from current profit snapshots.
    profit: ComparisonResponse
    #: Parcels behind ``profit`` by ACTUAL / ESTIMATED / MISSING.
    profit_quality: dict[str, int]
    received: ComparisonResponse
    receivable_paisa: int
    overdue_paisa: int
    #: COD on parcels still on the road. Not receivable yet.
    in_transit_paisa: int
    open_case_count: int
    open_case_paisa: int
    discrepancy_count: int
    discrepancy_paisa: int


class StockOverviewResponse(BaseModel):
    tracked_products: int
    total_units: int
    low_stock_items: int
    out_of_stock_items: int
    slow_moving_items: int
    slow_moving_days: int


class OverviewResponse(BaseModel):
    window: WindowResponse
    previous: WindowResponse
    orders: ComparisonResponse
    delivered: ComparisonResponse
    rto: OutcomeCountsResponse
    rto_previous: OutcomeCountsResponse
    #: UP / DOWN / FLAT, or INSUFFICIENT_DATA when either window is thin.
    rto_trend: str
    money: MoneyOverviewResponse | None
    money_locked: str | None
    stock: StockOverviewResponse | None
    explanations: list[ExplanationResponse]


class TrendBucketResponse(BaseModel):
    start: date
    end: date
    orders: int
    #: Parcels that reached an outcome (settled) in the bucket.
    parcels: int
    revenue_paisa: int | None
    profit_paisa: int | None


class TrendResponse(BaseModel):
    window: WindowResponse
    granularity: str
    buckets: list[TrendBucketResponse]
    profit_quality: dict[str, int] | None
    money_locked: str | None


class ProductInsightResponse(BaseModel):
    product_id: uuid.UUID | None
    name: str
    sku: str | None
    parcels: int
    units_delivered: int
    revenue_paisa: int | None
    #: ``null`` when some parcel has no product cost: not a figure at all.
    profit_paisa: int | None
    margin_basis_points: int | None
    profit_quality: str | None
    rto: OutcomeCountsResponse
    #: Line value of this product inside RTO parcels.
    rto_value_paisa: int
    stock_status: str
    stock_on_hand: int | None
    units_booked: int
    last_sale_at: datetime | None
    slow_moving: bool
    fast_moving: bool


class ProductInsightsResponse(BaseModel):
    window: WindowResponse
    category: str
    slow_moving_days: int
    fast_moving_min_units: int
    min_sample: int
    counts: dict[str, int]
    items: list[ProductInsightResponse]
    total: int
    offset: int
    limit: int
    has_more: bool
    money_locked: str | None


class PayoutDelayResponse(BaseModel):
    samples: int
    median_days: int
    reliable: bool


class CourierScorecardResponse(BaseModel):
    provider: str
    counts: OutcomeCountsResponse
    recent: OutcomeCountsResponse
    previous: OutcomeCountsResponse
    trend: str
    in_transit_now: int
    stuck_now: int
    outstanding_paisa: int | None
    overdue_paisa: int | None
    delivered_unpaid_paisa: int | None
    in_transit_paisa: int | None
    last_payment_on: date | None
    payout_delay: PayoutDelayResponse | None
    discrepancy_count: int | None
    discrepancy_paisa: int | None


class CourierInsightsResponse(BaseModel):
    window: WindowResponse
    items: list[CourierScorecardResponse]
    excluded_providers: list[str]
    min_sample: int
    money_locked: str | None


class AgingRowResponse(BaseModel):
    label: str
    min_days: int
    max_days: int | None
    count: int
    amount_paisa: int


class CourierCashResponse(BaseModel):
    provider: str
    outstanding_paisa: int
    overdue_paisa: int
    in_transit_paisa: int
    payout_delay: PayoutDelayResponse | None


class ForecastResponse(BaseModel):
    key: str
    parcel_count: int
    amount_paisa: int


class CashInsightsResponse(BaseModel):
    window: WindowResponse
    received: ComparisonResponse
    received_by_courier: dict[str, int]
    receivable_paisa: int
    overdue_paisa: int
    delivered_unpaid_paisa: int
    in_transit_paisa: int
    in_transit_count: int
    aging: list[AgingRowResponse]
    couriers: list[CourierCashResponse]
    #: ESTIMATE, from each courier's own payment history. Sums to receivable.
    forecast: list[ForecastResponse]
    explanations: list[ExplanationResponse]


class KindRowResponse(BaseModel):
    kind: str
    count: int
    amount_paisa: int


class CasePeriodResponse(BaseModel):
    start: date
    end: date
    opened: int
    resolved: int


class CourierDiscrepancyResponse(BaseModel):
    provider: str
    count: int
    amount_paisa: int


class ReconciliationInsightsResponse(BaseModel):
    window: WindowResponse
    matched: int
    discrepancy_count: int
    difference_paisa: int
    missing_cod_count: int
    missing_cod_paisa: int
    charge_mismatch_count: int
    charge_mismatch_paisa: int
    unmatched_count: int
    unmatched_paisa: int
    open_case_count: int
    open_case_paisa: int
    open_by_kind: list[KindRowResponse]
    periods: list[CasePeriodResponse]
    by_courier: list[CourierDiscrepancyResponse]
    explanations: list[ExplanationResponse]


class CustomerInsightsResponse(BaseModel):
    window: WindowResponse
    total_customers: int
    active: ComparisonResponse
    new: ComparisonResponse
    returning: int
    orders_with_customer: int
    repeat_orders: int
    repeat_order_rate_basis_points: int | None
    sufficient: bool
    min_sample: int
    repeat_customers: int
    multi_delivery_customers: int
    repeat_rto_customers: int
    explanations: list[ExplanationResponse]


class StockItemResponse(BaseModel):
    product_id: uuid.UUID
    variant_id: uuid.UUID | None
    name: str
    variant_name: str | None
    sku: str | None
    stock_on_hand: int
    low_stock_threshold: int | None
    status: str
    units_booked: int
    last_sale_at: datetime | None
    slow_moving: bool
    fast_moving: bool


class MovementRowResponse(BaseModel):
    reason: str
    units_in: int
    units_out: int


class InventoryInsightsResponse(BaseModel):
    window: WindowResponse
    filter: str
    slow_moving_days: int
    fast_moving_min_units: int
    tracked_products: int
    total_units: int
    low_stock_items: int
    out_of_stock_items: int
    counts: dict[str, int]
    movement: list[MovementRowResponse]
    items: list[StockItemResponse]
    total: int
    offset: int
    limit: int
    has_more: bool
    explanations: list[ExplanationResponse]
