from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from pydantic import ConfigDict
from pydantic import ValidationError as SchemaError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_hasher, get_vault
from app.api.v1.commerce_schemas import OrderCreatePayload
from app.common.idempotency import request_hash
from app.common.operation_lock import lock_shop
from app.core.errors import ConflictError, IdempotencyConflictError, ValidationError
from app.customers.service import CustomerService
from app.imports.templates import ORDERS_TEMPLATE, parse_row
from app.messaging.service import required
from app.order_sources.models import ExternalOrder, OrderSource
from app.orders.models import Order, OrderChannel
from app.orders.service import OrderDraft, OrderItemDraft, OrderService

PROVIDERS = ("CUSTOM_PUSH", "SHOPIFY", "WOOCOMMERCE", "MESSENGER")


#: Sources the Integrations Hub owns. Only its connectors may feed them, so a
#: pushed payload can never pose as a Shopify or WooCommerce order.
MANAGED = frozenset({"SHOPIFY", "WOOCOMMERCE"})


def capability(provider: str) -> dict[str, Any]:
    blocker = None
    if provider in MANAGED:
        blocker = "INTEGRATIONS_HUB_REQUIRED"
    elif provider != "CUSTOM_PUSH":
        blocker = "ORDER_SOURCE_OFFICIAL_CONTRACT_REQUIRED"
    return {"provider": provider, "available": blocker is None, "blocker": blocker}


class NativeOrder(OrderCreatePayload):
    model_config = ConfigDict(extra="forbid")


def validate_mapping(mapping: dict[str, str]) -> None:
    if mapping and (
        not {"phone", "product", "amount"}.issubset(mapping)
        or set(mapping) - set(ORDERS_TEMPLATE.required + ORDERS_TEMPLATE.optional)
        or any(not value.strip() or len(value) > 100 for value in mapping.values())
    ):
        raise ValidationError("Map phone, product and amount; use the existing order import fields")


def normalize(source: OrderSource, payload: dict[str, Any]) -> NativeOrder:
    validate_mapping(source.mapping)
    if source.mapping:
        if len(payload) > 100 or any(isinstance(v, (dict, list)) for v in payload.values()):
            raise ValidationError("Mapped rows must be flat")
        parsed = parse_row(ORDERS_TEMPLATE, payload, source.mapping)
        if not parsed.is_valid or any(w["field"] == "amount" for w in parsed.warnings):
            raise ValidationError(
                "Invalid external order row",
                details={
                    "errors": parsed.errors
                    or [{"field": "amount", "message": "An explicit amount is required"}]
                },
            )
        values = parsed.values
        quantity, total = values["quantity"], values["cod_amount_paisa"]
        if total % quantity:
            raise ValidationError(
                "Use native items when the total cannot be split into equal unit prices"
            )
        payload = {
            key: values[key]
            for key in (
                "phone",
                "customer_name",
                "address",
                "district",
                "area",
                "note",
                "cod_amount_paisa",
            )
        }
        payload["items"] = [
            {"name": values["product"], "quantity": quantity, "unit_price_paisa": total // quantity}
        ]
    if "client_id" in payload or ("channel" in payload and payload["channel"] != "API"):
        raise ValidationError("External order identity and channel are assigned by the server")
    try:
        result = NativeOrder.model_validate(payload)
    except SchemaError as exc:
        raise ValidationError(
            "Invalid external order",
            details={"fields": [".".join(map(str, e["loc"])) for e in exc.errors()]},
        ) from exc
    if len(result.items) > 100:
        raise ValidationError("At most 100 items per order")
    return result


async def create_native(
    db: AsyncSession, payload: OrderCreatePayload, *, client_id: uuid.UUID | None = None
) -> Order:
    service = OrderService(
        db, customers=CustomerService(db, hasher=get_hasher(), vault=get_vault())
    )
    values = payload.model_dump(exclude={"items", "channel", "client_id"})
    order, _ = await service.create(
        OrderDraft(
            **values,
            items=[OrderItemDraft(**item.model_dump()) for item in payload.items],
            channel=OrderChannel.API,
            client_id=client_id,
        )
    )
    return order


async def ingest(
    db: AsyncSession,
    source_id: uuid.UUID,
    external_order_id: str,
    payload: dict[str, Any],
    *,
    managed: bool = False,
) -> dict[str, Any]:
    """``managed`` is the Integrations Hub's connector path, and only that."""
    await lock_shop(db)
    source = await required(db, OrderSource, source_id)
    # Receipt identity is permanent. Mapping edits must not reinterpret a replay.
    digest = request_hash(payload)
    receipt = await db.scalar(
        sa.select(ExternalOrder).where(
            ExternalOrder.source_id == source.id,
            ExternalOrder.external_order_id == external_order_id,
        )
    )
    if receipt:
        changed = receipt.request_hash != digest
        if changed and not managed:
            raise IdempotencyConflictError(
                "External order ID was already used for a different payload"
            )
        # A store edits its own orders after we import them. The first import
        # stands; an edit is never a second order.
        return {
            "order_id": str(receipt.order_id),
            "external_order_id": receipt.external_order_id,
            "source_id": str(source.id),
            "replayed": True,
            **({"changed": True} if changed else {}),
        }
    if not source.enabled or not (
        (managed and source.provider in MANAGED) or capability(source.provider)["available"]
    ):
        raise ConflictError(
            "Order source is disabled",
            details={"blocker": capability(source.provider)["blocker"] or "ORDER_SOURCE_DISABLED"},
        )
    normalized = normalize(source, payload)
    # Deterministic, so even a lost receipt cannot mint a second order.
    client_id = (
        uuid.uuid5(uuid.NAMESPACE_URL, f"ecomsbd:source:{source.id}:{external_order_id}")
        if managed
        else None
    )
    order = await create_native(db, normalized, client_id=client_id)
    db.add(
        ExternalOrder(
            source_id=source.id,
            external_order_id=external_order_id,
            request_hash=digest,
            order_id=order.id,
        )
    )
    await db.flush()
    return {
        "order_id": str(order.id),
        "external_order_id": external_order_id,
        "source_id": str(source.id),
        "replayed": False,
    }
