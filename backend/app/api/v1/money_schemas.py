"""Request and response shapes for the money core.

Every amount on the wire is integer paisa (master spec section 32), and every
figure that is not settled says so. Section 123 and section 135: an estimated
value must never be presented as exact, and a screen that shows a number
without saying where it came from is a screen that will eventually be wrong
without anybody noticing.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.consignments.models import ConsignmentStatus
from app.payouts.models import AdjustmentType, PayoutLineStatus, PayoutStatus
from app.reconciliation.models import CaseKind, CaseStatus

__all__ = [
    "AgingBandResponse",
    "CaseResponse",
    "CaseUpdatePayload",
    "ConsignmentResponse",
    "DeliveryOutcomePayload",
    "DispatchPayload",
    "LedgerEntryResponse",
    "ManualCorrectionPayload",
    "ManualMatchPayload",
    "ManualPayoutPayload",
    "MoneySummaryResponse",
    "PayoutAdjustmentResponse",
    "PayoutLineResponse",
    "PayoutResponse",
    "ReceivableResponse",
    "ReconcileResponse",
    "StatementPreviewResponse",
    "UnmatchPayload",
]


# --------------------------------------------------------------------------- #
# Consignments — the manual courier flow
# --------------------------------------------------------------------------- #


class DispatchPayload(BaseModel):
    """Record that a parcel has gone out."""

    provider: str = Field(default="manual", max_length=40)
    tracking_code: str | None = Field(default=None, max_length=120)
    #: Overrides the order's COD. Useful when the seller agreed a different
    #: figure at the door.
    cod_amount_paisa: int | None = Field(default=None, ge=0)


class ItemOutcomePayload(BaseModel):
    consignment_item_id: uuid.UUID
    qty_delivered: int = Field(ge=0)
    qty_returned: int = Field(ge=0)


class DeliveryOutcomePayload(BaseModel):
    """How a parcel ended."""

    status: ConsignmentStatus
    occurred_at: datetime | None = None
    #: Required for a partial delivery: it is the only way to know what was
    #: actually collected (master spec section 17.8).
    items: list[ItemOutcomePayload] = Field(default_factory=list)
    note: str | None = Field(default=None, max_length=400)


class ConsignmentItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_item_id: uuid.UUID
    qty_shipped: int
    qty_delivered: int
    qty_returned: int
    unit_collectible_paisa: int


class ConsignmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_id: uuid.UUID
    provider: str
    provider_consignment_id: str | None
    tracking_code: str | None
    merchant_reference: str
    status: str
    cod_amount_paisa: int
    #: What is genuinely collectible from the delivered units. Zero until the
    #: parcel is delivered — never the booked COD in advance.
    collectible_paisa: int
    booked_at: datetime | None
    delivered_at: datetime | None
    returned_at: datetime | None
    items: list[ConsignmentItemResponse] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Receivables
# --------------------------------------------------------------------------- #


class ReceivableResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    consignment_id: uuid.UUID
    order_id: uuid.UUID
    provider: str
    status: str
    collectible_paisa: int
    settled_paisa: int
    deduction_paisa: int
    adjustment_paisa: int
    #: Collectible plus adjustments, minus what has arrived and what the
    #: provider took (section 50).
    outstanding_paisa: int
    eligible_at: datetime | None
    settled_at: datetime | None
    status_reason: str | None
    version: int
    created_at: datetime

    #: Days since the money became collectible. ``null`` while it is not.
    age_days: int | None = None
    #: The seller-facing order number, so the list does not need a join.
    order_number: str | None = None


class AgingBandResponse(BaseModel):
    label: str
    min_days: int
    max_days: int | None
    parcel_count: int
    outstanding_paisa: int


class MoneySummaryResponse(BaseModel):
    """The Money screen's headline figures.

    Every one of these is derived from the ledger or from the receivables, not
    from a maintained counter, so section 81.10 holds by construction: the
    dashboard cannot drift from the rows behind it.
    """

    #: What couriers are holding right now.
    outstanding_paisa: int
    #: What has actually arrived, over the period.
    settled_paisa: int
    #: Delivered parcels whose money has not arrived.
    unpaid_parcel_count: int
    #: What providers deducted, by bucket, over the period.
    courier_charge_paisa: int
    cod_fee_paisa: int
    return_charge_paisa: int
    #: Deductions we could not classify. Kept separate rather than folded in
    #: (section 84).
    unknown_deduction_paisa: int
    #: Money given up.
    write_off_paisa: int
    #: Payout money that has not been tied to any parcel yet.
    unexplained_payout_paisa: int
    open_case_count: int
    aging: list[AgingBandResponse] = Field(default_factory=list)
    #: The window these figures cover, in Asia/Dhaka business dates.
    since: date | None = None
    until: date | None = None


class LedgerEntryResponse(BaseModel):
    """One line of the money history.

    This is the "explain this number" payload: a seller asking why a parcel
    shows ৳0 outstanding gets the delivery, the payment and any deduction in
    the order they happened.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    occurred_at: datetime
    business_date: date
    entity_type: str
    entity_id: uuid.UUID
    event_type: str
    amount_paisa: int
    direction: str
    bucket: str
    source: str
    source_ref: str | None
    reversal_of: uuid.UUID | None
    reason: str | None


class ManualCorrectionPayload(BaseModel):
    """A seller correcting a balance by hand (master spec section 134).

    A reason is mandatory and there is no field for "new balance": a
    correction states the *change*, and the resulting balance is derived. Never
    directly update a settled total.
    """

    amount_paisa: int = Field(gt=0)
    #: True to credit the seller, false to debit.
    increases_balance: bool
    reason: str = Field(min_length=5, max_length=400)


class WriteOffPayload(BaseModel):
    reason: str = Field(min_length=5, max_length=400)


class DisputePayload(BaseModel):
    reason: str = Field(min_length=5, max_length=400)


# --------------------------------------------------------------------------- #
# Payouts
# --------------------------------------------------------------------------- #


class ManualPayoutPayload(BaseModel):
    provider: str = Field(default="manual", max_length=40)
    total_paisa: int = Field(gt=0)
    paid_on: date | None = None
    reference: str | None = Field(default=None, max_length=160)
    note: str | None = Field(default=None, max_length=400)


class PayoutLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    row_number: int
    amount_paisa: int
    applied_paisa: int
    status: PayoutLineStatus
    confidence: str | None
    provider_consignment_id: str | None
    tracking_code: str | None
    merchant_reference: str | None
    delivered_on: date | None
    receivable_id: uuid.UUID | None
    match_reason: str | None
    #: The raw row as parsed, so a seller can see exactly what the file said.
    raw: dict[str, Any] = Field(default_factory=dict)
    #: What the engine considered and why, kept so a refusal is explainable.
    candidates: list[Any] = Field(default_factory=list)


class PayoutAdjustmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    payout_line_id: uuid.UUID | None
    type: AdjustmentType
    amount_paisa: int
    provider_label: str | None
    raw_text: str | None
    recognized_rule: str | None


class PayoutResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider: str
    provider_reference: str | None
    source: str
    status: PayoutStatus
    total_paisa: int
    applied_paisa: int
    #: What arrived but has not been tied to a parcel.
    unexplained_paisa: int
    paid_on: date | None
    received_at: datetime
    note: str | None
    source_file_id: uuid.UUID | None
    created_at: datetime


class PayoutDetailResponse(PayoutResponse):
    lines: list[PayoutLineResponse] = Field(default_factory=list)
    adjustments: list[PayoutAdjustmentResponse] = Field(default_factory=list)


class StatementRowPreview(BaseModel):
    row_number: int
    amount_paisa: int | None
    consignment_id: str | None
    tracking_code: str | None
    merchant_reference: str | None
    delivered_on: date | None
    fee_paisa: int | None
    fee_label: str | None
    errors: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class StatementPreviewResponse(BaseModel):
    """What a statement contains, before anything is created."""

    detected_headers: list[str] = Field(default_factory=list)
    column_mapping: dict[str, str] = Field(default_factory=dict)
    row_count: int
    invalid_row_count: int
    total_paisa: int
    rows: list[StatementRowPreview] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Reconciliation
# --------------------------------------------------------------------------- #


class ReconcileResponse(BaseModel):
    """Section 82's own summary: *"38 exact matches / 2 suggested / 1 unresolved"*."""

    payout_id: uuid.UUID
    #: True when the run changed nothing (section 112's shadow mode).
    shadow: bool
    exact_matches: int
    suggested: int
    unresolved: int
    applied_paisa: int
    cases_opened: int


class ManualMatchPayload(BaseModel):
    receivable_id: uuid.UUID
    #: Mandatory. Section 81.7: a manual match records who matched it and why.
    reason: str = Field(min_length=3, max_length=400)
    #: Defaults to whatever is left on the line.
    amount_paisa: int | None = Field(default=None, gt=0)


class UnmatchPayload(BaseModel):
    reason: str = Field(min_length=3, max_length=400)


class CaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: CaseKind
    status: CaseStatus
    priority: str
    subject_type: str
    subject_id: uuid.UUID
    amount_paisa: int
    summary: str
    detail: dict[str, Any] = Field(default_factory=dict)
    receivable_id: uuid.UUID | None
    payout_id: uuid.UUID | None
    payout_line_id: uuid.UUID | None
    consignment_id: uuid.UUID | None
    opened_at: datetime
    resolved_at: datetime | None
    resolution: str | None
    created_at: datetime


class CaseUpdatePayload(BaseModel):
    status: CaseStatus
    #: Required when resolving or dismissing. A case closed with no explanation
    #: teaches nobody anything, and the same problem returns next month.
    resolution: str | None = Field(default=None, max_length=400)
