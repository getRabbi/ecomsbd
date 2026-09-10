"""Product and inventory endpoints.

Master spec section 39 (CRUD) plus the stock-movement routes, which exist
because section 10.4 forbids treating stock as a settable number: a seller
corrects stock by recording an adjustment, and the movement history is a
first-class thing they can read.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import DbSession, TenantPrincipal, require_permission
from app.api.v1.commerce_schemas import (
    ProductCreatePayload,
    ProductResponse,
    ProductUpdatePayload,
    StockAdjustmentPayload,
    StockMovementResponse,
)
from app.common.pagination import Page, decode_cursor
from app.products.models import Product, StockMovement, StockMovementSource
from app.products.service import ProductService, StockAdjustment, StockService
from app.tenants.roles import Permission

router = APIRouter(prefix="/products", tags=["products"])


async def _products(db: DbSession) -> ProductService:
    return ProductService(db)


async def _stock(db: DbSession) -> StockService:
    return StockService(db)


ProductServiceDep = Annotated[ProductService, Depends(_products)]
StockServiceDep = Annotated[StockService, Depends(_stock)]


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
    )


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
) -> Page[ProductResponse]:
    rows = await products.list_products(
        limit=limit,
        cursor=decode_cursor(cursor) if cursor else None,
        search=search,
        include_archived=include_archived,
        low_stock_only=low_stock_only,
    )
    return Page[ProductResponse].build(rows, limit=limit, serializer=_to_response)


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


@router.get(
    "/{product_id}/stock-movements",
    response_model=Page[StockMovementResponse],
    summary="Stock movement history",
    dependencies=[Depends(require_permission(Permission.PRODUCT_VIEW))],
)
async def list_stock_movements(
    product_id: uuid.UUID,
    principal: TenantPrincipal,
    products: ProductServiceDep,
    stock: StockServiceDep,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> Page[StockMovementResponse]:
    """Every change to this product's stock, newest first.

    The audit trail behind the number on the product card. A seller asking
    "where did my stock go?" gets an answer with a reason and a date.
    """
    # Resolve the product first so a missing one is a 404 rather than an empty
    # page, and so the tenant guard rejects another shop's product id.
    await products.get(product_id)
    rows = await stock.history(
        product_id, limit=limit, cursor=decode_cursor(cursor) if cursor else None
    )

    def serialize(movement: StockMovement) -> StockMovementResponse:
        return StockMovementResponse.model_validate(movement)

    return Page[StockMovementResponse].build(rows, limit=limit, serializer=serialize)


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
    products: ProductServiceDep,
    stock: StockServiceDep,
) -> StockMovementResponse:
    """Record a manual stock correction.

    Appends to the ledger; it never sets a total. Overselling requires an
    explicit ``allow_negative``, so a seller cannot drift into negative stock
    without saying so (master spec section 76).
    """
    await products.get(product_id)
    movement = await stock.record_movement(
        StockAdjustment(
            product_id=product_id,
            quantity_delta=payload.quantity_delta,
            reason=payload.reason,
            source=StockMovementSource.SELLER,
            note=payload.note,
        ),
        allow_negative=payload.allow_negative,
    )
    return StockMovementResponse.model_validate(movement)


__all__ = ["router"]
