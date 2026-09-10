"""Wire shapes for analytics, expenses and the notification centre.

Every amount is integer paisa (master spec section 32) and every rate is basis
points, so nothing on the wire is a float that two clients could round
differently.

Section 135 governs the rest: a response that carries a figure also carries
how much of it was measured. ``estimated_parcels``, ``quality`` and
``has_enough_sample`` exist so a screen can never render an estimate in the
same typeface as a settled number.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.expenses.models import AllocationMethod, ExpenseKind
from app.notifications.models import NotificationKind, Severity
from app.profit.models import ChargeKind, ChargeSource

__all__ = [
    "AlertResponse",
    "AllocationRequest",
    "AllocationResponse",
    "ChargeCreatePayload",
    "ChargeResponse",
    "ExpenseCreatePayload",
    "ExpenseResponse",
    "HomeResponse",
    "NotificationResponse",
    "ProductLineResponse",
    "ProfitResponse",
    "RateLineResponse",
    "ReturnsResponse",
    "UnreadCountResponse",
    "WeeklySummaryResponse",
]


# --------------------------------------------------------------------------- #
# Analytics
# --------------------------------------------------------------------------- #


class AlertResponse(BaseModel):
    """One of section 23's four lines."""

    kind: NotificationKind
    severity: Severity
    count: int
    amount_paisa: int


class HomeResponse(BaseModel):
    """Section 1.1's daily money control screen."""

    as_of: date
    orders_today: int
    delivered_today: int
    returned_today: int
    gross_sales_paisa: int
    realized_revenue_paisa: int
    contribution_profit_paisa: int
    cod_outstanding_paisa: int
    cod_expected_today_paisa: int
    #: Outstanding COD the shop has no settlement history to date yet.
    cod_unforecast_paisa: int
    cod_overdue_paisa: int
    mismatch_paisa: int
    mismatch_count: int
    return_loss_paisa: int
    estimated_parcels: int
    incomplete_parcels: int
    alerts: list[AlertResponse]


class ProfitResponse(BaseModel):
    since: date
    until: date
    parcel_count: int
    realized_revenue_paisa: int
    item_cost_paisa: int
    delivery_charge_paisa: int
    cod_fee_paisa: int
    return_charge_paisa: int
    packaging_paisa: int
    ad_cost_paisa: int
    write_off_cost_paisa: int
    contribution_profit_paisa: int
    unallocated_ad_spend_paisa: int
    fixed_cost_paisa: int
    operating_profit_paisa: int
    #: ``None`` when nothing was collected, rather than a zero margin, which
    #: would read as "we sold at cost".
    margin_basis_points: int | None
    #: ``{"ACTUAL": 12, "ESTIMATED": 3, "MISSING": 0}`` — section 135.
    quality: dict[str, int]


class RateLineResponse(BaseModel):
    label: str
    parcel_count: int
    return_count: int
    return_rate_basis_points: int
    loss_paisa: int
    #: False means "shown for completeness, do not act on it" (section 24).
    has_enough_sample: bool


class ReturnsResponse(BaseModel):
    """Section 19's return economics."""

    since: date
    until: date
    parcel_count: int
    return_count: int
    return_rate_basis_points: int | None
    direct_loss_paisa: int
    outward_delivery_cost_paisa: int
    return_delivery_cost_paisa: int
    packaging_loss_paisa: int
    write_off_paisa: int
    by_reason: dict[str, int]
    unknown_reason_count: int
    by_product: list[RateLineResponse]
    by_area: list[RateLineResponse]
    by_courier: list[RateLineResponse]


class ProductLineResponse(BaseModel):
    product_name: str
    parcel_count: int
    units_delivered: int
    revenue_paisa: int
    profit_paisa: int
    return_count: int
    margin_basis_points: int | None
    has_enough_sample: bool


# --------------------------------------------------------------------------- #
# Expenses
# --------------------------------------------------------------------------- #


class ExpenseCreatePayload(BaseModel):
    """Money the seller spent (master spec section 86).

    Recording is not allocating. Nothing here changes a profit figure until
    the allocation endpoint is called, which is why the period is required:
    without it there is no way to decide which parcels an ad spend could
    possibly belong to.
    """

    kind: ExpenseKind
    amount_paisa: int = Field(gt=0)
    period_start: date
    period_end: date
    description: str = Field(min_length=1, max_length=200)
    #: Only meaningful for a per-unit product-tagged allocation.
    product_id: uuid.UUID | None = None
    preferred_method: AllocationMethod = AllocationMethod.EQUAL_PER_DELIVERED_ORDER


class ExpenseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    amount_paisa: int
    allocated_paisa: int
    unallocated_paisa: int
    period_start: date
    period_end: date
    description: str
    product_id: uuid.UUID | None
    preferred_method: str
    allocated_at: datetime | None
    created_at: datetime


class AllocationRequest(BaseModel):
    """Spread one expense over the parcels it belongs to."""

    method: AllocationMethod | None = None
    #: Required when re-allocating. Section 88: a profit figure the seller has
    #: already seen only changes through a correction that says why.
    reason: str | None = Field(default=None, max_length=400)


class AllocationResponse(BaseModel):
    expense_id: uuid.UUID
    method: str
    version: int
    parcel_count: int
    allocated_paisa: int
    #: Non-zero means the spend reached no parcel — usually a period with no
    #: deliveries. Surfaced rather than swallowed.
    unallocated_paisa: int
    reached_nothing: bool


# --------------------------------------------------------------------------- #
# Notification centre
# --------------------------------------------------------------------------- #


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    severity: str
    title: str
    body: str
    entity_type: str | None
    entity_id: uuid.UUID | None
    amount_paisa: int
    item_count: int
    business_date: date
    read_at: datetime | None
    payload: dict[str, Any]
    created_at: datetime


class UnreadCountResponse(BaseModel):
    unread: int


class WeeklySummaryResponse(BaseModel):
    """The Friday figures (section 23)."""

    week_start: date
    week_end: date
    order_count: int
    delivered_count: int
    return_count: int
    return_loss_paisa: int
    sales_paisa: int
    contribution_profit_paisa: int
    cod_outstanding_paisa: int
    overdue_paisa: int
    mismatch_count: int
    ad_spend_paisa: int
    return_rate_basis_points: int | None
    #: ``[name, profit_paisa]``, or ``null`` when the sample is too small to
    #: rank honestly — in which case ``ranking_note`` says so.
    best_product: tuple[str, int] | None
    worst_product: tuple[str, int] | None
    #: ``[provider, delivery_success_basis_points]``.
    best_courier: tuple[str, int] | None
    worst_courier: tuple[str, int] | None
    ranking_note: str | None
    courier_note: str | None
    alerts: list[AlertResponse]


# --------------------------------------------------------------------------- #
# Charges
# --------------------------------------------------------------------------- #


class ChargeCreatePayload(BaseModel):
    """What a parcel cost (master spec section 17.3).

    ``source`` is required and has no default. In manual courier mode the
    seller is the only source of a delivery charge, and a figure typed from a
    receipt is not the same claim as one read off a settlement statement —
    section 85's hierarchy depends on the difference being recorded rather
    than assumed.
    """

    kind: ChargeKind
    amount_paisa: int = Field(ge=0)
    source: ChargeSource
    provider_label: str | None = Field(default=None, max_length=80)
    source_ref: str | None = Field(default=None, max_length=200)
    #: Required only to replace a charge that came from a better source —
    #: section 17.3's audited correction.
    reason: str | None = Field(default=None, max_length=400)
    occurred_at: datetime | None = None


class ChargeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    consignment_id: uuid.UUID
    kind: str
    source: str
    amount_paisa: int
    provider_label: str | None
    source_ref: str | None
    reason: str | None
    occurred_at: datetime
    superseded_by: uuid.UUID | None
