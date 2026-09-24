from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Header, Query, Response
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import DbSession, get_hasher, get_vault
from app.api.v1.commerce_schemas import (
    CustomerCreatePayload,
    ProductCreatePayload,
    StockAdjustmentPayload,
)
from app.api.v1.order_sources import IngestInput
from app.common.idempotency import request_hash
from app.core.errors import ValidationError
from app.customers.models import Customer
from app.customers.service import CustomerService
from app.integrations import custom_website
from app.messaging.service import required
from app.order_sources.service import NativeOrder, create_native, ingest
from app.orders.models import Order
from app.products.models import Product, ProductVariant
from app.products.service import ProductService
from app.public_api.auth import PublicPrincipal
from app.public_api.service import write_once

router = APIRouter(prefix="/public/v1", tags=["public API v1"])
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=120)]


def fields(row: object, names: str) -> dict:
    return jsonable_encoder({key: getattr(row, key) for key in names.split()})


ORDER_FIELDS = "id order_number customer_id customer_name customer_phone_masked status channel cod_amount_paisa created_at updated_at"
CUSTOMER_FIELDS = "id name phone_masked created_at updated_at"
PRODUCT_FIELDS = "id name sku default_selling_price_paisa cost_paisa stock_tracking_enabled stock_on_hand is_active created_at updated_at"


@router.get("/me")
async def me(principal: PublicPrincipal) -> dict[str, Any]:
    """Which key this is and what it may do (V3.8). Needs no scope; creates nothing."""
    return {
        "key_id": str(principal.key_id),
        "shop_id": str(principal.tenant_id),
        "scopes": sorted(principal.scopes),
        "rate_limit_per_minute": principal.rate_limit,
        "expires_at": principal.expires_at.isoformat() if principal.expires_at else None,
    }


@router.get("/orders")
async def orders(
    principal: PublicPrincipal,
    db: DbSession,
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    principal.require("orders:read")
    rows = (
        await db.scalars(
            sa.select(Order)
            .where(Order.deleted_at.is_(None))
            .order_by(Order.created_at.desc(), Order.id)
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return {"items": [fields(row, ORDER_FIELDS) for row in rows], "next_offset": offset + len(rows)}


@router.get("/orders/{order_id}")
async def order_detail(
    order_id: uuid.UUID, principal: PublicPrincipal, db: DbSession
) -> dict[str, Any]:
    principal.require("orders:read")
    row = await required(db, Order, order_id)
    return {
        **fields(row, ORDER_FIELDS),
        "items": [
            fields(
                item,
                "id product_id product_name variant_id quantity unit_price_paisa discount_paisa line_total_paisa",
            )
            for item in row.items
        ],
    }


@router.post("/orders", status_code=201)
async def create_order(
    body: NativeOrder, principal: PublicPrincipal, db: DbSession, key: Key
) -> dict[str, Any]:
    principal.require("orders:write")
    if body.client_id is not None or len(body.items) > 100:
        raise ValidationError("Use Idempotency-Key; at most 100 items per order")

    async def execute() -> dict[str, Any]:
        return fields(await create_native(db, body), ORDER_FIELDS)

    return await write_once(
        db, principal, "orders.create", key, body.model_dump(mode="json"), execute
    )


@router.get("/customers")
async def customers(
    principal: PublicPrincipal,
    db: DbSession,
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    principal.require("customers:read")
    rows = (
        await db.scalars(
            sa.select(Customer)
            .where(Customer.deleted_at.is_(None))
            .order_by(Customer.created_at.desc(), Customer.id)
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return {
        "items": [fields(row, CUSTOMER_FIELDS) for row in rows],
        "next_offset": offset + len(rows),
    }


@router.post("/customers", status_code=201)
async def create_customer(
    body: CustomerCreatePayload, principal: PublicPrincipal, db: DbSession, key: Key
) -> dict[str, Any]:
    principal.require("customers:write")

    async def execute() -> dict[str, Any]:
        service = CustomerService(db, hasher=get_hasher(), vault=get_vault())
        return fields(await service.create(**body.model_dump()), CUSTOMER_FIELDS)

    return await write_once(
        db, principal, "customers.create", key, body.model_dump(mode="json"), execute
    )


@router.get("/products")
async def products(
    principal: PublicPrincipal,
    db: DbSession,
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    sku: str | None = Query(None, min_length=1, max_length=64),
) -> dict[str, Any]:
    principal.require("products:read")
    query = sa.select(Product).where(Product.is_active.is_(True))
    if sku is not None:
        # A website maps its catalogue by SKU: the product's own, or a variant's.
        variant_owner = sa.select(ProductVariant.product_id).where(ProductVariant.sku == sku)
        query = query.where(sa.or_(Product.sku == sku, Product.id.in_(variant_owner)))
    rows = (
        await db.scalars(
            query.order_by(Product.created_at.desc(), Product.id).offset(offset).limit(limit)
        )
    ).all()
    items = [fields(row, PRODUCT_FIELDS) for row in rows]
    if sku is not None and rows:
        variants = (
            await db.scalars(
                sa.select(ProductVariant).where(
                    ProductVariant.product_id.in_([row.id for row in rows])
                )
            )
        ).all()
        for item in items:
            item["variants"] = [
                fields(v, "id name sku stock_on_hand is_active")
                for v in variants
                if str(v.product_id) == item["id"]
            ]
    return {"items": items, "next_offset": offset + len(rows)}


@router.post("/products", status_code=201)
async def create_product(
    body: ProductCreatePayload, principal: PublicPrincipal, db: DbSession, key: Key
) -> dict[str, Any]:
    principal.require("products:write")
    if body.opening_stock:
        principal.require("inventory:write")

    async def execute() -> dict[str, Any]:
        return fields(await ProductService(db).create(**body.model_dump()), PRODUCT_FIELDS)

    return await write_once(
        db, principal, "products.create", key, body.model_dump(mode="json"), execute
    )


@router.get("/inventory/{product_id}")
async def inventory(
    product_id: uuid.UUID, principal: PublicPrincipal, db: DbSession
) -> dict[str, Any]:
    principal.require("inventory:read")
    row = await required(db, Product, product_id)
    variants = (
        await db.scalars(sa.select(ProductVariant).where(ProductVariant.product_id == product_id))
    ).all()
    return {
        **fields(row, "id name stock_tracking_enabled stock_on_hand"),
        "variants": [fields(v, "id name stock_on_hand") for v in variants],
    }


@router.post("/inventory/{product_id}/adjustments", status_code=201)
async def adjust_inventory(
    product_id: uuid.UUID,
    body: StockAdjustmentPayload,
    principal: PublicPrincipal,
    db: DbSession,
    key: Key,
) -> dict[str, Any]:
    principal.require("inventory:write")
    if body.idempotency_key is not None:
        raise ValidationError("Use the Idempotency-Key header")

    async def execute() -> dict[str, Any]:
        movement = await ProductService(db).adjust_stock(
            product_id,
            quantity_delta=body.quantity_delta,
            reason=body.reason,
            note=body.note,
            variant_id=body.variant_id,
            allow_negative=body.allow_negative,
            client_key="api:" + request_hash([str(principal.key_id), str(product_id), key]),
        )
        return fields(
            movement, "id product_id variant_id quantity_delta balance_after reason created_at"
        )

    return await write_once(
        db, principal, f"inventory/{product_id}", key, body.model_dump(mode="json"), execute
    )


@router.post("/sources/{source_id}/orders", status_code=201)
async def source_order(
    source_id: uuid.UUID, body: IngestInput, principal: PublicPrincipal, db: DbSession, key: Key
) -> dict[str, Any]:
    principal.require("sources:write")
    principal.require("orders:write")

    async def execute() -> dict[str, Any]:
        return await ingest(db, source_id, body.external_order_id, body.payload)

    return await write_once(
        db, principal, f"sources/{source_id}", key, body.model_dump(mode="json"), execute
    )


class OrderStatusInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["CONFIRMED", "CANCELLED"]
    reason: str | None = Field(default=None, max_length=200)


@router.post("/orders/{order_id}/status")
async def order_status(
    order_id: uuid.UUID,
    body: OrderStatusInput,
    principal: PublicPrincipal,
    db: DbSession,
    key: Key,
    response: Response,
) -> dict[str, Any]:
    """Confirm or cancel from the website. A booked parcel is not cancelled here:
    the answer is 409 CANCELLED_AFTER_BOOKING and the seller gets a conflict."""
    principal.require("orders:write")

    async def execute() -> dict[str, Any]:
        return await custom_website.apply_status(
            db, principal.key_id, order_id, body.status, body.reason
        )

    result = await write_once(
        db, principal, f"orders/{order_id}/status", key, body.model_dump(mode="json"), execute
    )
    if result.get("result") == "CONFLICT":
        response.status_code = 409
    return result
