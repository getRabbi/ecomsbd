"""Request and response schemas for the commerce core.

Two contract rules run through every model here.

**Money is integer paisa on the wire.** Never a float, never a formatted string
(master spec section 32). Parsing a display string back into a number is exactly
where rounding errors enter a money system.

**A phone number leaves the server masked.** ``01712****78`` and never the full
number, unless the caller used the explicit, audited reveal endpoint
(sections 101, 133).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.customers.models import CustomerFlag
from app.imports.models import ImportRowStatus, ImportStatus, ImportTemplate
from app.orders.models import OrderChannel, OrderStatus
from app.products.models import StockMovementReason

__all__ = [
    "CustomerCreatePayload",
    "CustomerDetailResponse",
    "CustomerResponse",
    "CustomerUpdatePayload",
    "DuplicateCheckResponse",
    "ImportCommitResponse",
    "ImportResponse",
    "ImportRowResponse",
    "OrderCreatePayload",
    "OrderCreateResponse",
    "OrderDetailResponse",
    "OrderItemPayload",
    "OrderItemResponse",
    "OrderResponse",
    "OrderUpdatePayload",
    "ParseRequest",
    "ParseResponse",
    "ProductCreatePayload",
    "ProductResponse",
    "ProductUpdatePayload",
    "StockAdjustmentPayload",
    "StockMovementResponse",
    "SyncChangesResponse",
    "SyncMutationPayload",
    "SyncMutationResult",
    "SyncPushResponse",
]


# --------------------------------------------------------------------------- #
# Products
# --------------------------------------------------------------------------- #


class ProductCreatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    sku: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=4000)
    cost_paisa: int = Field(default=0, ge=0)
    default_selling_price_paisa: int = Field(default=0, ge=0)
    stock_tracking_enabled: bool = True
    opening_stock: int = Field(default=0, ge=0)
    low_stock_threshold: int | None = Field(default=None, ge=0)


class ProductUpdatePayload(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    sku: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=4000)
    cost_paisa: int | None = Field(default=None, ge=0)
    default_selling_price_paisa: int | None = Field(default=None, ge=0)
    low_stock_threshold: int | None = Field(default=None, ge=0)
    is_active: bool | None = None
    archived: bool | None = None


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    sku: str | None
    description: str | None
    cost_paisa: int
    default_selling_price_paisa: int
    stock_tracking_enabled: bool
    stock_on_hand: int
    low_stock_threshold: int | None
    is_low_stock: bool
    is_active: bool
    is_archived: bool
    created_at: datetime
    updated_at: datetime


class StockAdjustmentPayload(BaseModel):
    """A hand adjustment to stock.

    ``reason`` is restricted to the two a seller may perform directly. Every
    other reason is produced by the system as a consequence of an order or a
    courier event, and accepting it here would let a client fabricate a booking
    decrement that never happened.
    """

    quantity_delta: int = Field(description="Signed. Negative removes stock.")
    reason: StockMovementReason = StockMovementReason.MANUAL_ADJUSTMENT
    note: str | None = Field(default=None, max_length=400)
    allow_negative: bool = False

    @field_validator("quantity_delta")
    @classmethod
    def _non_zero(cls, value: int) -> int:
        if value == 0:
            raise ValueError("quantity_delta must not be zero")
        return value

    @field_validator("reason")
    @classmethod
    def _seller_performable(cls, value: StockMovementReason) -> StockMovementReason:
        allowed = {
            StockMovementReason.MANUAL_ADJUSTMENT,
            StockMovementReason.OPENING,
            StockMovementReason.DAMAGED_WRITE_OFF,
        }
        if value not in allowed:
            raise ValueError(f"{value} is produced by the system, not by a direct adjustment")
        return value


class StockMovementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    quantity_delta: int
    balance_after: int
    reason: str
    source: str
    note: str | None
    order_id: uuid.UUID | None
    consignment_id: uuid.UUID | None
    occurred_at: datetime
    created_at: datetime


# --------------------------------------------------------------------------- #
# Customers
# --------------------------------------------------------------------------- #


class CustomerCreatePayload(BaseModel):
    phone: str = Field(min_length=6, max_length=24)
    name: str | None = Field(default=None, max_length=160)
    address: str | None = Field(default=None, max_length=1000)


class CustomerUpdatePayload(BaseModel):
    name: str | None = Field(default=None, max_length=160)
    notes: str | None = Field(default=None, max_length=4000)
    flag: CustomerFlag | None = None
    flag_reason: str | None = Field(default=None, max_length=400)


class CustomerAddressResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    raw_address: str
    normalized_address: str | None
    label: str | None
    district: str | None
    area: str | None
    is_default: bool


class CustomerResponse(BaseModel):
    """List shape. Carries the masked phone only."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str | None
    phone_masked: str
    phone_last4: str
    flag: str
    is_repeat_buyer: bool
    order_count: int
    delivered_count: int
    returned_count: int
    cancelled_count: int
    #: ``None`` when there is no terminal history yet — showing 0% for a new
    #: customer would read as a judgement the data does not support.
    success_rate_basis_points: int | None
    realized_revenue_paisa: int
    first_order_at: datetime | None
    last_order_at: datetime | None
    created_at: datetime


class CustomerDetailResponse(CustomerResponse):
    notes: str | None
    flag_reason: str | None
    addresses: list[CustomerAddressResponse] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Orders
# --------------------------------------------------------------------------- #


class OrderItemPayload(BaseModel):
    product_id: uuid.UUID | None = None
    #: Required when there is no product — section 20 allows a free-text line.
    name: str | None = Field(default=None, max_length=200)
    quantity: int = Field(default=1, ge=1, le=9999)
    unit_price_paisa: int | None = Field(default=None, ge=0)
    discount_paisa: int = Field(default=0, ge=0)
    variant_label: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=300)


class OrderCreatePayload(BaseModel):
    phone: str = Field(min_length=6, max_length=24)
    items: list[OrderItemPayload] = Field(min_length=1)
    customer_name: str | None = Field(default=None, max_length=160)
    address: str | None = Field(default=None, max_length=1000)
    district: str | None = Field(default=None, max_length=80)
    area: str | None = Field(default=None, max_length=120)
    cod_amount_paisa: int | None = Field(default=None, ge=0)
    discount_paisa: int = Field(default=0, ge=0)
    delivery_fee_paisa: int = Field(default=0, ge=0)
    note: str | None = Field(default=None, max_length=4000)
    #: The pasted text this order came from, kept verbatim (section 96).
    source_text: str | None = Field(default=None, max_length=8000)
    channel: OrderChannel = OrderChannel.MANUAL
    #: Device-minted id. Makes an offline create replay-safe.
    client_id: uuid.UUID | None = None


class OrderUpdatePayload(BaseModel):
    items: list[OrderItemPayload] | None = None
    cod_amount_paisa: int | None = Field(default=None, ge=0)
    discount_paisa: int | None = Field(default=None, ge=0)
    delivery_fee_paisa: int | None = Field(default=None, ge=0)
    note: str | None = Field(default=None, max_length=4000)
    address: str | None = Field(default=None, max_length=1000)
    district: str | None = Field(default=None, max_length=80)
    area: str | None = Field(default=None, max_length=120)
    status: OrderStatus | None = None
    cancellation_reason: str | None = Field(default=None, max_length=200)
    #: Version the client read. A mismatch is reported as a conflict rather
    #: than overwriting a concurrent edit (section 37).
    expected_version: int | None = None


class OrderItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID | None
    product_name: str
    sku: str | None
    variant_label: str | None
    quantity: int
    unit_price_paisa: int
    unit_cost_snapshot_paisa: int
    discount_paisa: int
    line_total_paisa: int
    note: str | None


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_number: str
    client_id: uuid.UUID
    customer_id: uuid.UUID | None
    customer_name: str | None
    customer_phone_masked: str | None
    delivery_address_raw: str | None
    delivery_district: str | None
    delivery_area: str | None
    status: str
    channel: str
    business_date: date
    subtotal_paisa: int
    discount_paisa: int
    delivery_fee_paisa: int
    cod_amount_paisa: int
    note: str | None
    version: int
    created_at: datetime
    updated_at: datetime

    #: Placeholders until the engines that produce them exist. Explicitly not
    #: fabricated: an order card must not show a courier state or a profit
    #: figure the system has not computed.
    fulfillment_state: str = "NOT_BOOKED"
    risk_state: str = "NOT_CHECKED"
    profit_state: str = "PENDING_CALCULATION"


class OrderDetailResponse(OrderResponse):
    items: list[OrderItemResponse] = Field(default_factory=list)
    source_text: str | None = None
    estimated_item_cost_paisa: int = 0


class DuplicateCandidateResponse(BaseModel):
    order_id: uuid.UUID
    order_number: str
    status: str
    cod_amount_paisa: int
    hours_ago: float
    reasons: list[str]
    matching_item_names: list[str] = Field(default_factory=list)
    is_strong: bool


class DuplicateCheckResponse(BaseModel):
    """A warning, never a block (master spec section 9)."""

    possible_duplicate: bool
    message: str = ""
    window_hours: int = 24
    candidates: list[DuplicateCandidateResponse] = Field(default_factory=list)


class OrderCreateResponse(BaseModel):
    order: OrderDetailResponse
    duplicate_check: DuplicateCheckResponse | None = None


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #


class ParseRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


class ParsedItemResponse(BaseModel):
    name: str
    quantity: int
    size: str | None = None
    color: str | None = None
    unit_price_paisa: int | None = None


class ParseResponse(BaseModel):
    """Master spec section 96's normalized response.

    Nothing here is saved or booked. The seller confirms or edits every field
    (section 7.1), and low-confidence fields come back empty rather than
    guessed.
    """

    customer_name: str | None = None
    phones: list[str] = Field(default_factory=list)
    #: Set only when exactly one number was found. Several means the seller
    #: chooses (section 8).
    selected_phone: str | None = None
    address: str | None = None
    items: list[ParsedItemResponse] = Field(default_factory=list)
    cod_amount_paisa: int | None = None
    notes: str | None = None
    confidence: dict[str, float] = Field(default_factory=dict)
    parser: str = "deterministic"
    warnings: list[str] = Field(default_factory=list)
    needs_phone_selection: bool = False
    is_low_confidence: bool = False
    #: Echoed back so a failed parse never loses what the seller pasted.
    source_text: str = ""


# --------------------------------------------------------------------------- #
# Imports
# --------------------------------------------------------------------------- #


class ImportRowResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    row_number: int
    status: ImportRowStatus
    raw: dict[str, Any]
    parsed: dict[str, Any]
    errors: list[Any] = Field(default_factory=list)
    warnings: list[Any] = Field(default_factory=list)
    created_entity_id: uuid.UUID | None = None


class ImportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    template: ImportTemplate
    status: ImportStatus
    original_filename: str
    source_sha256: str
    detected_headers: list[Any] = Field(default_factory=list)
    column_mapping: dict[str, Any] = Field(default_factory=dict)
    row_count: int
    ready_count: int
    warning_count: int
    duplicate_count: int
    invalid_count: int
    created_count: int
    can_commit: bool = False
    dry_run_at: datetime | None = None
    committed_at: datetime | None = None
    failure_reason: str | None = None
    created_at: datetime


class ImportCommitResponse(BaseModel):
    import_batch: ImportResponse
    created_count: int
    skipped_count: int


# --------------------------------------------------------------------------- #
# Sync
# --------------------------------------------------------------------------- #


class SyncMutationPayload(BaseModel):
    """One queued client mutation (master spec sections 37, 38)."""

    mutation_id: uuid.UUID
    entity_type: str = Field(max_length=32)
    entity_id: uuid.UUID
    operation: str = Field(max_length=16)
    payload: dict[str, Any] = Field(default_factory=dict)
    #: Version the device held. Absent for a create.
    base_version: int | None = None
    client_timestamp: datetime | None = None


class SyncPushRequest(BaseModel):
    mutations: list[SyncMutationPayload] = Field(max_length=100)
    device_id: uuid.UUID | None = None


class SyncMutationResult(BaseModel):
    mutation_id: uuid.UUID
    status: str
    entity_id: uuid.UUID | None = None
    server_version: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    #: Present on a conflict, so the client can show "yours / theirs"
    #: (section 128).
    server_state: dict[str, Any] | None = None


class SyncPushResponse(BaseModel):
    results: list[SyncMutationResult] = Field(default_factory=list)
    applied_count: int = 0
    conflict_count: int = 0
    rejected_count: int = 0
    duplicate_count: int = 0


class SyncChangeEntry(BaseModel):
    entity_type: str
    entity_id: uuid.UUID
    #: True for a tombstone: the record was deleted and the device should drop
    #: it rather than resurrect it (section 38).
    deleted: bool = False
    version: int | None = None
    updated_at: datetime
    data: dict[str, Any] | None = None


class SyncChangesResponse(BaseModel):
    changes: list[SyncChangeEntry] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False
    #: Server time at the moment the page was produced, so the client can show
    #: "last synced" without trusting its own clock (section 69).
    server_time: datetime
