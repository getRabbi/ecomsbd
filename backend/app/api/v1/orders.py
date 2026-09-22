"""Order endpoints (master spec section 39).

``POST /v1/orders`` creates an order and **does not book a courier**. Booking is
a separate explicit action that ships in Phase C. Section 62.17 is the reason:
an external courier create is irreversible, so it never happens as a side effect
of saving a record.
"""

from __future__ import annotations

import uuid
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import selectinload

from app.api.deps import (
    DbSession,
    SettingsDep,
    TenantPrincipal,
    get_hasher,
    get_vault,
    require_permission,
)
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
from app.consignments.models import Consignment
from app.customers.service import CustomerService
from app.orders.duplicates import DuplicateCheck
from app.orders.models import Order, OrderStatus
from app.orders.parser import DeterministicOrderParser
from app.orders.service import OrderDraft, OrderItemDraft, OrderService
from app.tenants.roles import Permission

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
                variant_id=item.variant_id,
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
            variant_id=item.variant_id,
        )
        for item in payload_items
    ]


@router.get(
    "",
    response_model=Page[OrderResponse],
    summary="List orders",
    dependencies=[Depends(require_permission(Permission.ORDER_VIEW))],
)
async def list_orders(
    principal: TenantPrincipal,
    orders: OrderServiceDep,
    db: DbSession,
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
    couriers = await _courier_for_orders(db, [order.id for order in rows])

    def serialize(order: Order) -> OrderResponse:
        response = _to_response(order)
        found = couriers.get(order.id)
        if found is not None:
            response.courier_provider, response.tracking_code = found
        return response

    return Page[OrderResponse].build(rows, limit=limit, serializer=serialize)


async def _courier_for_orders(
    db: DbSession, order_ids: list[uuid.UUID]
) -> dict[uuid.UUID, tuple[str, str | None]]:
    """The courier and tracking code for each order's current parcel.

    One bounded query for the whole page rather than a join on the list query
    or a lookup per row: the list query is the hottest read in the product and
    is left exactly as it was, and fifty orders cost one extra round trip
    instead of fifty.

    An order with several consignments — one cancelled, one reshipped — takes
    the newest, which is the parcel a seller means when they ask "where is it?".
    """
    if not order_ids:
        return {}

    rows = (
        await db.execute(
            sa.select(
                Consignment.order_id,
                Consignment.provider,
                Consignment.tracking_code,
                Consignment.created_at,
            )
            .where(Consignment.order_id.in_(order_ids))
            .order_by(Consignment.order_id, Consignment.created_at.desc())
        )
    ).all()

    found: dict[uuid.UUID, tuple[str, str | None]] = {}
    for order_id, provider, tracking_code, _created_at in rows:
        # Ordered newest first, so the first row seen for an order is the one
        # that counts and later ones are its history.
        if order_id not in found:
            found[order_id] = (provider, tracking_code)
    return found


@router.post(
    "",
    response_model=OrderCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an order",
    dependencies=[Depends(require_permission(Permission.ORDER_WRITE))],
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
    dependencies=[Depends(require_permission(Permission.ORDER_WRITE))],
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
    dependencies=[Depends(require_permission(Permission.ORDER_VIEW))],
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


@router.get(
    "/{order_id}",
    response_model=OrderDetailResponse,
    summary="Read an order",
    dependencies=[Depends(require_permission(Permission.ORDER_VIEW))],
)
async def get_order(
    order_id: uuid.UUID,
    principal: TenantPrincipal,
    orders: OrderServiceDep,
    db: DbSession,
) -> OrderDetailResponse:
    detail = _to_detail(await orders.get(order_id))
    # The newest parcel, so the order screen can offer "receive the return"
    # without a second round trip. One indexed query.
    parcel = (
        await db.execute(
            sa.select(Consignment)
            .where(Consignment.order_id == order_id)
            .options(selectinload(Consignment.items))
            .order_by(Consignment.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if parcel is not None:
        detail.consignment_id = parcel.id
        detail.consignment_status = parcel.status
        detail.courier_provider = parcel.provider
        detail.tracking_code = parcel.tracking_code
        detail.return_pending_units = sum(item.qty_return_pending for item in parcel.items)
    return detail


@router.patch(
    "/{order_id}",
    response_model=OrderDetailResponse,
    summary="Update an order",
    dependencies=[Depends(require_permission(Permission.ORDER_WRITE))],
)
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
