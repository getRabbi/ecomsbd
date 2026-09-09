"""Order endpoints (master spec section 39).

``POST /v1/orders`` creates an order and **does not book a courier**. Booking is
a separate explicit action that ships in Phase C. Section 62.17 is the reason:
an external courier create is irreversible, so it never happens as a side effect
of saving a record.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from app.api.deps import DbSession, SettingsDep, TenantPrincipal, get_hasher, get_vault
from app.api.v1.commerce_schemas import (
    DuplicateCandidateResponse,
    DuplicateCheckResponse,
    OrderCreatePayload,
    OrderCreateResponse,
    OrderDetailResponse,
    OrderItemResponse,
    OrderResponse,
    OrderUpdatePayload,
    ParsedItemResponse,
    ParseRequest,
    ParseResponse,
)
from app.common.pagination import Page, decode_cursor
from app.customers.service import CustomerService
from app.orders.duplicates import DuplicateCheck
from app.orders.models import Order, OrderStatus
from app.orders.parser import DeterministicOrderParser
from app.orders.service import OrderDraft, OrderItemDraft, OrderService

router = APIRouter(prefix="/orders", tags=["orders"])

_parser = DeterministicOrderParser()


async def _orders(db: DbSession, settings: SettingsDep) -> OrderService:
    customers = CustomerService(db, hasher=get_hasher(settings), vault=get_vault(settings))
    return OrderService(db, customers=customers)


OrderServiceDep = Annotated[OrderService, Depends(_orders)]


def _to_response(order: Order) -> OrderResponse:
    return OrderResponse.model_validate(order, from_attributes=True)


def _to_detail(order: Order) -> OrderDetailResponse:
    return OrderDetailResponse(
        **_to_response(order).model_dump(),
        items=[
            OrderItemResponse(
                id=item.id,
                product_id=item.product_id,
                product_name=item.product_name,
                sku=item.sku,
                variant_label=item.variant_label,
                quantity=item.quantity,
                unit_price_paisa=item.unit_price_paisa,
                unit_cost_snapshot_paisa=item.unit_cost_snapshot_paisa,
                discount_paisa=item.discount_paisa,
                line_total_paisa=item.line_total_paisa,
                note=item.note,
            )
            for item in order.items
        ],
        source_text=order.source_text,
        estimated_item_cost_paisa=order.estimated_item_cost_paisa,
    )


def _to_duplicate_response(check: DuplicateCheck) -> DuplicateCheckResponse:
    return DuplicateCheckResponse(
        possible_duplicate=check.possible_duplicate,
        message=check.message,
        window_hours=check.window_hours,
        candidates=[
            DuplicateCandidateResponse(
                order_id=candidate.order_id,
                order_number=candidate.order_number,
                status=str(candidate.status),
                cod_amount_paisa=candidate.cod_amount_paisa,
                hours_ago=candidate.hours_ago,
                reasons=[str(reason) for reason in candidate.reasons],
                matching_item_names=list(candidate.matching_item_names),
                is_strong=candidate.is_strong,
            )
            for candidate in check.candidates
        ],
    )


def _to_item_drafts(payload_items: list) -> list[OrderItemDraft]:
    return [
        OrderItemDraft(
            product_id=item.product_id,
            name=item.name,
            quantity=item.quantity,
            unit_price_paisa=item.unit_price_paisa,
            discount_paisa=item.discount_paisa,
            variant_label=item.variant_label,
            note=item.note,
        )
        for item in payload_items
    ]


@router.get("", response_model=Page[OrderResponse], summary="List orders")
async def list_orders(
    principal: TenantPrincipal,
    orders: OrderServiceDep,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    order_status: Annotated[OrderStatus | None, Query(alias="status")] = None,
    customer_id: Annotated[uuid.UUID | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=120)] = None,
) -> Page[OrderResponse]:
    """List orders newest first.

    Search covers the order number, the customer name and an exact phone, with
    Bangla numerals normalised first (master spec section 129).
    """
    rows = await orders.list_orders(
        limit=limit,
        cursor=decode_cursor(cursor) if cursor else None,
        status=order_status,
        customer_id=customer_id,
        search=search,
    )
    return Page[OrderResponse].build(rows, limit=limit, serializer=_to_response)


@router.post(
    "",
    response_model=OrderCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an order",
)
async def create_order(
    payload: OrderCreatePayload,
    principal: TenantPrincipal,
    orders: OrderServiceDep,
) -> OrderCreateResponse:
    """Create an order in ``DRAFT``.

    Returns the order together with any duplicate warning. The warning never
    blocks creation (master spec section 9): a customer ordering twice in a day
    is a real workflow, and the seller is the one who can tell the difference.

    No courier is contacted.
    """
    order, duplicates = await orders.create(
        OrderDraft(
            phone=payload.phone,
            items=_to_item_drafts(payload.items),
            customer_name=payload.customer_name,
            address=payload.address,
            district=payload.district,
            area=payload.area,
            cod_amount_paisa=payload.cod_amount_paisa,
            discount_paisa=payload.discount_paisa,
            delivery_fee_paisa=payload.delivery_fee_paisa,
            note=payload.note,
            source_text=payload.source_text,
            channel=payload.channel,
            client_id=payload.client_id,
        )
    )
    return OrderCreateResponse(
        order=_to_detail(order),
        duplicate_check=_to_duplicate_response(duplicates) if duplicates else None,
    )


@router.post(
    "/parse",
    response_model=ParseResponse,
    summary="Parse pasted order text",
)
async def parse_order_text(
    payload: ParseRequest,
    principal: TenantPrincipal,
) -> ParseResponse:
    """Extract order fields from pasted Messenger or WhatsApp text.

    Deterministic and offline — no model, no network. Nothing is created or
    booked; the seller confirms and edits every field before saving
    (master spec section 7.1). A field the parser is unsure about comes back
    empty rather than guessed, and the original text is always echoed so a
    failed parse loses nothing.
    """
    parsed = _parser.parse(payload.text)
    return ParseResponse(
        customer_name=parsed.customer_name,
        phones=parsed.phones,
        selected_phone=parsed.selected_phone,
        address=parsed.address,
        items=[
            ParsedItemResponse(
                name=item.name,
                quantity=item.quantity,
                size=item.size,
                color=item.color,
                unit_price_paisa=item.unit_price_paisa,
            )
            for item in parsed.items
        ],
        cod_amount_paisa=parsed.cod_amount_paisa,
        notes=parsed.notes,
        confidence=parsed.confidence,
        parser=parsed.parser,
        warnings=parsed.warnings,
        needs_phone_selection=parsed.needs_phone_selection,
        is_low_confidence=parsed.is_low_confidence,
        source_text=parsed.source_text,
    )


class DuplicateProbeRequest(BaseModel):
    phone: str = Field(min_length=6, max_length=24)
    cod_amount_paisa: int = Field(ge=0)
    item_names: list[str] = Field(default_factory=list)
    exclude_order_id: uuid.UUID | None = None


@router.post(
    "/check-duplicates",
    response_model=DuplicateCheckResponse,
    summary="Check for a similar recent order",
)
async def check_duplicates(
    payload: DuplicateProbeRequest,
    principal: TenantPrincipal,
    orders: OrderServiceDep,
) -> DuplicateCheckResponse:
    """Look for a similar recent order before the seller commits.

    Called while the order form is still open so the warning arrives in time to
    be useful. Advisory only.
    """
    check = await orders.check_duplicates_for(
        phone=payload.phone,
        cod_amount_paisa=payload.cod_amount_paisa,
        item_names=payload.item_names,
        exclude_order_id=payload.exclude_order_id,
    )
    return _to_duplicate_response(check)


@router.get("/{order_id}", response_model=OrderDetailResponse, summary="Read an order")
async def get_order(
    order_id: uuid.UUID,
    principal: TenantPrincipal,
    orders: OrderServiceDep,
) -> OrderDetailResponse:
    return _to_detail(await orders.get(order_id))


@router.patch("/{order_id}", response_model=OrderDetailResponse, summary="Update an order")
async def update_order(
    order_id: uuid.UUID,
    payload: OrderUpdatePayload,
    principal: TenantPrincipal,
    orders: OrderServiceDep,
) -> OrderDetailResponse:
    """Edit an order, and/or move it to a new status.

    Fields are applied first, then the transition, so a seller can correct an
    order and confirm it in one call. An illegal transition is rejected rather
    than silently ignored.
    """
    has_field_changes = any(
        value is not None
        for value in (
            payload.items,
            payload.cod_amount_paisa,
            payload.discount_paisa,
            payload.delivery_fee_paisa,
            payload.note,
            payload.address,
            payload.district,
            payload.area,
        )
    )

    if has_field_changes:
        await orders.update(
            order_id,
            items=_to_item_drafts(payload.items) if payload.items is not None else None,
            cod_amount_paisa=payload.cod_amount_paisa,
            discount_paisa=payload.discount_paisa,
            delivery_fee_paisa=payload.delivery_fee_paisa,
            note=payload.note,
            address=payload.address,
            district=payload.district,
            area=payload.area,
            expected_version=payload.expected_version,
        )

    if payload.status is not None:
        await orders.transition(order_id, payload.status, reason=payload.cancellation_reason)

    return _to_detail(await orders.get(order_id))


__all__ = ["router"]
