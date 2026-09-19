"""Product and inventory endpoints.

Master spec section 39 (CRUD) plus the stock-movement routes, which exist
because section 10.4 forbids treating stock as a settable number: a seller
corrects stock by recording an adjustment, and the movement history is a
first-class thing they can read.

V2.2 adds optional variants, restock entry, a filtered history and a shop-wide
stock summary. Every mutation here appends to the ledger; none sets a total.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Header, Query, status

from app.api.deps import DbSession, TenantPrincipal, require_permission
from app.api.v1.commerce_schemas import (
    ProductCreatePayload,
    ProductResponse,
    ProductUpdatePayload,
    ProductVariantResponse,
    RestockPayload,
    StockAdjustmentPayload,
    StockMovementResponse,
    StockSummaryResponse,
    VariantCreatePayload,
    VariantUpdatePayload,
)
from app.common.pagination import Page, decode_cursor
from app.orders.models import Order
from app.products.models import Product, ProductVariant, StockMovement, StockMovementReason
from app.products.service import ProductService, StockService
from app.tenants.roles import Permission
from app.users.models import User

router = APIRouter(prefix="/products", tags=["products"])


async def _products(db: DbSession) -> ProductService:
    return ProductService(db)


async def _stock(db: DbSession) -> StockService:
    return StockService(db)


ProductServiceDep = Annotated[ProductService, Depends(_products)]
StockServiceDep = Annotated[StockService, Depends(_stock)]
#: The conventional header; a body ``idempotency_key`` wins when both are sent.
IdempotencyHeader = Annotated[str | None, Header(alias="Idempotency-Key", max_length=80)]


def _to_response(product: Product) -> ProductResponse:
    return ProductResponse(
        id=product.id,
        name=product.name,
        sku=product.sku,
        description=product.description,
        cost_paisa=product.cost_paisa,
        default_selling_price_paisa=product.default_selling_price_paisa,
        stock_tracking_enabled=product.stock_tracking_enabled,
        stock_on_hand=product.stock_on_hand,
        low_stock_threshold=product.low_stock_threshold,
        is_low_stock=product.is_low_stock,
        is_active=product.is_active,
        is_archived=product.is_archived,
        created_at=product.created_at,
        updated_at=product.updated_at,
        has_variants=product.has_variants,
        variants=[ProductVariantResponse.model_validate(v) for v in product.variants],
    )


async def _movement_responses(
    db: DbSession, movements: list[StockMovement]
) -> dict[uuid.UUID, StockMovementResponse]:
    """Movements with their variant, order number and actor, in three queries.

    Batched per page rather than looked up per row, so a 100-row history page
    costs the same as a 1-row one.
    """
    variant_ids = {m.variant_id for m in movements if m.variant_id}
    order_ids = {m.order_id for m in movements if m.order_id}
    actor_ids = {m.actor_user_id for m in movements if m.actor_user_id}

    variants: dict[uuid.UUID, str] = {}
    if variant_ids:
        variants = {
            row[0]: row[1]
            for row in await db.execute(
                sa.select(ProductVariant.id, ProductVariant.name).where(
                    ProductVariant.id.in_(variant_ids)
                )
            )
        }
    orders: dict[uuid.UUID, str] = {}
    if order_ids:
        orders = {
            row[0]: row[1]
            for row in await db.execute(
                sa.select(Order.id, Order.order_number).where(Order.id.in_(order_ids))
            )
        }
    actors: dict[uuid.UUID, str | None] = {}
    if actor_ids:
        actors = {
            row[0]: row[1]
            for row in await db.execute(
                sa.select(User.id, User.display_name).where(User.id.in_(actor_ids))
            )
        }

    return {
        movement.id: StockMovementResponse.model_validate(movement).model_copy(
            update={
                "variant_name": variants.get(movement.variant_id) if movement.variant_id else None,
                "order_number": orders.get(movement.order_id) if movement.order_id else None,
                "actor_name": actors.get(movement.actor_user_id)
                if movement.actor_user_id
                else None,
            }
        )
        for movement in movements
    }


async def _one_movement(db: DbSession, movement: StockMovement) -> StockMovementResponse:
    return (await _movement_responses(db, [movement]))[movement.id]


@router.get(
    "",
    response_model=Page[ProductResponse],
    summary="List products",
    dependencies=[Depends(require_permission(Permission.PRODUCT_VIEW))],
)
async def list_products(
    principal: TenantPrincipal,
    products: ProductServiceDep,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    search: Annotated[str | None, Query(max_length=120)] = None,
    include_archived: Annotated[bool, Query()] = False,
    low_stock_only: Annotated[bool, Query()] = False,
    out_of_stock_only: Annotated[bool, Query()] = False,
) -> Page[ProductResponse]:
    rows = await products.list_products(
        limit=limit,
        cursor=decode_cursor(cursor) if cursor else None,
        search=search,
        include_archived=include_archived,
        low_stock_only=low_stock_only,
        out_of_stock_only=out_of_stock_only,
    )
    return Page[ProductResponse].build(rows, limit=limit, serializer=_to_response)


@router.get(
    "/stock-summary",
    response_model=StockSummaryResponse,
    summary="Shop-wide stock position",
    dependencies=[Depends(require_permission(Permission.PRODUCT_VIEW))],
)
async def stock_summary(
    principal: TenantPrincipal,
    products: ProductServiceDep,
) -> StockSummaryResponse:
    summary = await products.stock_summary()
    return StockSummaryResponse(
        tracked_products=summary.tracked_products,
        total_units=summary.total_units,
        low_stock_items=summary.low_stock_items,
        out_of_stock_items=summary.out_of_stock_items,
    )


@router.post(
    "",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a product",
    dependencies=[Depends(require_permission(Permission.PRODUCT_WRITE))],
)
async def create_product(
    payload: ProductCreatePayload,
    principal: TenantPrincipal,
    products: ProductServiceDep,
) -> ProductResponse:
    product = await products.create(
        name=payload.name,
        sku=payload.sku,
        description=payload.description,
        cost_paisa=payload.cost_paisa,
        default_selling_price_paisa=payload.default_selling_price_paisa,
        stock_tracking_enabled=payload.stock_tracking_enabled,
        opening_stock=payload.opening_stock,
        low_stock_threshold=payload.low_stock_threshold,
    )
    return _to_response(product)


@router.get(
    "/{product_id}",
    response_model=ProductResponse,
    summary="Read a product",
    dependencies=[Depends(require_permission(Permission.PRODUCT_VIEW))],
)
async def get_product(
    product_id: uuid.UUID,
    principal: TenantPrincipal,
    products: ProductServiceDep,
) -> ProductResponse:
    return _to_response(await products.get(product_id))


@router.patch(
    "/{product_id}",
    response_model=ProductResponse,
    summary="Update a product",
    dependencies=[Depends(require_permission(Permission.PRODUCT_WRITE))],
)
async def update_product(
    product_id: uuid.UUID,
    payload: ProductUpdatePayload,
    principal: TenantPrincipal,
    products: ProductServiceDep,
) -> ProductResponse:
    product = await products.update(
        product_id,
        name=payload.name,
        sku=payload.sku,
        description=payload.description,
        cost_paisa=payload.cost_paisa,
        default_selling_price_paisa=payload.default_selling_price_paisa,
        low_stock_threshold=payload.low_stock_threshold,
        is_active=payload.is_active,
        archived=payload.archived,
    )
    return _to_response(product)


@router.post(
    "/{product_id}/variants",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a variant",
    dependencies=[Depends(require_permission(Permission.PRODUCT_WRITE))],
)
async def add_variant(
    product_id: uuid.UUID,
    payload: VariantCreatePayload,
    principal: TenantPrincipal,
    products: ProductServiceDep,
) -> ProductResponse:
    """Add "Black / M" to a product. Returns the product with all its variants.

    The first variant turns a simple product into a variant product; any stock
    it held is moved out by one explained movement, to be counted into the
    variants.
    """
    await products.add_variant(
        product_id,
        name=payload.name,
        sku=payload.sku,
        options=payload.options,
        price_paisa=payload.price_paisa,
        cost_paisa=payload.cost_paisa,
        low_stock_threshold=payload.low_stock_threshold,
        opening_stock=payload.opening_stock,
    )
    return _to_response(await products.get(product_id))


@router.patch(
    "/{product_id}/variants/{variant_id}",
    response_model=ProductVariantResponse,
    summary="Update a variant",
    dependencies=[Depends(require_permission(Permission.PRODUCT_WRITE))],
)
async def update_variant(
    product_id: uuid.UUID,
    variant_id: uuid.UUID,
    payload: VariantUpdatePayload,
    principal: TenantPrincipal,
    products: ProductServiceDep,
) -> ProductVariantResponse:
    variant = await products.update_variant(
        product_id,
        variant_id,
        name=payload.name,
        sku=payload.sku,
        price_paisa=payload.price_paisa,
        cost_paisa=payload.cost_paisa,
        low_stock_threshold=payload.low_stock_threshold,
        is_active=payload.is_active,
    )
    return ProductVariantResponse.model_validate(variant)


@router.get(
    "/{product_id}/stock-movements",
    response_model=Page[StockMovementResponse],
    summary="Stock movement history",
    dependencies=[Depends(require_permission(Permission.PRODUCT_VIEW))],
)
async def list_stock_movements(
    product_id: uuid.UUID,
    principal: TenantPrincipal,
    db: DbSession,
    products: ProductServiceDep,
    stock: StockServiceDep,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    variant_id: Annotated[uuid.UUID | None, Query()] = None,
    reason: Annotated[list[StockMovementReason] | None, Query()] = None,
    occurred_from: Annotated[datetime | None, Query()] = None,
    occurred_to: Annotated[datetime | None, Query()] = None,
) -> Page[StockMovementResponse]:
    """Every change to this product's stock, newest first.

    The audit trail behind the number on the product card. A seller asking
    "where did my stock go?" gets an answer with a reason, a date, the order
    or reference behind it and who did it.
    """
    # Resolve the product first so a missing one is a 404 rather than an empty
    # page, and so the tenant guard rejects another shop's product id.
    await products.get(product_id)
    rows = await stock.history(
        product_id,
        limit=limit,
        cursor=decode_cursor(cursor) if cursor else None,
        variant_id=variant_id,
        reasons=reason,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
    )
    enriched = await _movement_responses(db, rows)
    return Page[StockMovementResponse].build(
        rows, limit=limit, serializer=lambda movement: enriched[movement.id]
    )


@router.post(
    "/{product_id}/stock-adjustments",
    response_model=StockMovementResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Adjust stock by hand",
    dependencies=[Depends(require_permission(Permission.INVENTORY_ADJUST))],
)
async def adjust_stock(
    product_id: uuid.UUID,
    payload: StockAdjustmentPayload,
    principal: TenantPrincipal,
    db: DbSession,
    products: ProductServiceDep,
    idempotency_key: IdempotencyHeader = None,
) -> StockMovementResponse:
    """Record a manual stock correction.

    Appends to the ledger; it never sets a total. Overselling requires an
    explicit ``allow_negative``, so a seller cannot drift into negative stock
    without saying so (master spec section 76). A repeated request id records
    one movement.
    """
    movement = await products.adjust_stock(
        product_id,
        quantity_delta=payload.quantity_delta,
        reason=payload.reason,
        note=payload.note,
        variant_id=payload.variant_id,
        allow_negative=payload.allow_negative,
        client_key=payload.idempotency_key or idempotency_key,
    )
    return await _one_movement(db, movement)


@router.post(
    "/{product_id}/restocks",
    response_model=StockMovementResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record a restock",
    dependencies=[Depends(require_permission(Permission.INVENTORY_ADJUST))],
)
async def restock(
    product_id: uuid.UUID,
    payload: RestockPayload,
    principal: TenantPrincipal,
    db: DbSession,
    products: ProductServiceDep,
    idempotency_key: IdempotencyHeader = None,
) -> StockMovementResponse:
    """New goods arrived: quantity, optional unit cost, reference and date.

    Not a purchase order, supplier record or payable — only a positive,
    explained movement.
    """
    movement = await products.restock(
        product_id,
        quantity=payload.quantity,
        variant_id=payload.variant_id,
        unit_cost_paisa=payload.unit_cost_paisa,
        update_cost=payload.update_cost,
        reference=payload.reference,
        note=payload.note,
        received_at=payload.received_at,
        client_key=payload.idempotency_key or idempotency_key,
    )
    return await _one_movement(db, movement)


__all__ = ["router"]
