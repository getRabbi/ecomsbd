"""Custom Website: the V2 Public API and outbound webhooks, packaged for a seller.

Nothing here is a new API. A connection is a CUSTOM_PUSH order source, a
shop-scoped API key limited to what a storefront needs, and optionally one
signed webhook endpoint. The website calls
``POST /public/v1/sources/{source_id}/orders`` and gets V2's idempotency,
normalization and order pipeline.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import AuditAction
from app.core.clock import utc_now
from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.integrations import service, sync
from app.integrations.models import IntegrationConnection
from app.order_sources.models import ExternalOrder, OrderSource
from app.public_api.models import ApiKey, WebhookDelivery, WebhookEndpoint
from app.public_api.service import issue_key
from app.public_api.webhooks import TOPICS, create_endpoint, queue_test_delivery

#: A storefront reads and writes its own orders and reads the catalogue and
#: stock to map its SKUs. Changing stock is opt-in: STOCK_WRITE.
SCOPES = ["orders:read", "orders:write", "sources:write", "products:read", "inventory:read"]
STOCK_WRITE = "inventory:write"
DEFAULT_TOPICS = [
    "order.confirmed",
    "order.cancelled",
    "order.fulfilled",
    "tracking.assigned",
    "order.delivered",
    "order.returned",
    "inventory.updated",
]
RATE_LIMIT = 120


def api_base_url() -> str:
    return get_settings().public_base_url.rstrip("/") + "/public/v1"


async def _new_key(
    db: AsyncSession, conn: IntegrationConnection, actor_id: uuid.UUID, *, stock_write: bool = False
) -> tuple[ApiKey, str]:
    return await issue_key(
        db,
        name=f"{conn.name} (website)"[:100],
        scopes=SCOPES + ([STOCK_WRITE] if stock_write else []),
        rate_limit=RATE_LIMIT,
        created_by=actor_id,
    )


async def create(
    db: AsyncSession, name: str, actor_id: uuid.UUID
) -> tuple[IntegrationConnection, str]:
    conn = await service.create(db, "CUSTOM_WEBSITE", name, actor_id)
    source = OrderSource(name=name[:120], provider="CUSTOM_PUSH", enabled=True, mapping={})
    db.add(source)
    await db.flush()
    key, token = await _new_key(db, conn, actor_id)
    conn.source_id = source.id
    conn.webhook_token = None  # Inbound traffic is the Public API, not a callback.
    conn.config = {"api_key_id": str(key.id), "scopes": SCOPES}
    return conn, token


async def _key(db: AsyncSession, conn: IntegrationConnection) -> ApiKey | None:
    key_id = conn.config.get("api_key_id")
    return await db.get(ApiKey, uuid.UUID(key_id)) if key_id else None


async def _endpoint(db: AsyncSession, conn: IntegrationConnection) -> WebhookEndpoint | None:
    endpoint_id = conn.config.get("webhook_endpoint_id")
    return await db.get(WebhookEndpoint, uuid.UUID(endpoint_id)) if endpoint_id else None


async def rotate_key(
    db: AsyncSession, conn: IntegrationConnection, actor_id: uuid.UUID, *, stock_write: bool = False
) -> str:
    """Issue a new key and revoke the old one now. The website must be updated."""
    if conn.state == "DISCONNECTED":
        raise ConflictError("This connection is disconnected")
    old = await _key(db, conn)
    if old is not None and old.revoked_at is None:
        old.revoked_at = utc_now()
    key, token = await _new_key(db, conn, actor_id, stock_write=stock_write)
    conn.config = {**conn.config, "api_key_id": str(key.id), "scopes": key.scopes}
    await service.audit(db, AuditAction.INTEGRATION_KEY_ROTATED, conn, stock_write=stock_write)
    return token


async def set_webhook(
    db: AsyncSession, conn: IntegrationConnection, url: str, topics: list[str] | None
) -> str:
    """Point the connection's signed events at ``url``. Returns the new secret once."""
    if conn.state == "DISCONNECTED":
        raise ConflictError("This connection is disconnected")
    chosen = topics or DEFAULT_TOPICS
    if set(chosen) - TOPICS:
        raise ValidationError("Unknown webhook topic")
    old = await _endpoint(db, conn)
    if old is not None:
        old.enabled = False
    endpoint, secret = await create_endpoint(db, url, chosen)
    conn.config = {**conn.config, "webhook_endpoint_id": str(endpoint.id)}
    await service.audit(db, AuditAction.INTEGRATION_CONFIGURED, conn, change="webhook")
    return secret


async def test_webhook(
    db: AsyncSession, conn: IntegrationConnection, shop_id: uuid.UUID
) -> WebhookDelivery:
    endpoint = await _endpoint(db, conn)
    if endpoint is None or not endpoint.enabled:
        raise ConflictError("Add a webhook address first", details={"code": "WEBHOOK_NOT_SET"})
    return await queue_test_delivery(db, endpoint, shop_id)


async def go_live(db: AsyncSession, conn: IntegrationConnection) -> None:
    key = await _key(db, conn)
    if key is None or key.revoked_at is not None:
        raise ConflictError("Create an API key first", details={"code": "API_KEY_REQUIRED"})
    await service.set_source_enabled(db, conn, True)
    await service.went_live(db, conn)


async def disconnect(db: AsyncSession, conn: IntegrationConnection) -> None:
    key = await _key(db, conn)
    if key is not None and key.revoked_at is None:
        key.revoked_at = utc_now()
    endpoint = await _endpoint(db, conn)
    if endpoint is not None:
        endpoint.enabled = False
    await service.disconnect(db, conn, remote=False)


async def facts(db: AsyncSession, conn: IntegrationConnection) -> dict[str, Any]:
    """What the setup checklist and health read. All local; no network."""
    key = await _key(db, conn)
    endpoint = await _endpoint(db, conn)
    first = await db.scalar(
        sa.select(ExternalOrder)
        .where(ExternalOrder.source_id == conn.source_id)
        .order_by(ExternalOrder.created_at.desc())
        .limit(1)
    )
    deliveries: dict[str, Any] = {"failed_24h": 0, "last_status": None, "last_at": None}
    if endpoint is not None:
        latest = await db.scalar(
            sa.select(WebhookDelivery)
            .where(WebhookDelivery.endpoint_id == endpoint.id)
            .order_by(WebhookDelivery.created_at.desc())
            .limit(1)
        )
        failed = await db.scalar(
            sa.select(sa.func.count())
            .select_from(WebhookDelivery)
            .where(
                WebhookDelivery.endpoint_id == endpoint.id,
                WebhookDelivery.status.in_(["FAILED", "RETRY"]),
                WebhookDelivery.created_at >= utc_now() - timedelta(days=1),
            )
        )
        deliveries = {
            "failed_24h": failed or 0,
            "last_status": latest.status if latest else None,
            "last_at": latest.updated_at if latest else None,
        }
    return {
        "key_active": key is not None and key.revoked_at is None,
        "key_created_at": key.created_at if key else None,
        "last_api_call_at": key.last_used_at if key else None,
        "last_order_at": first.created_at if first else None,
        "last_order_id": first.order_id if first else None,
        "webhook_url": endpoint.url if endpoint else None,
        "webhook_enabled": bool(endpoint and endpoint.enabled),
        "webhook_topics": endpoint.topics if endpoint else [],
        "deliveries": deliveries,
    }


def overlay(view: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    """Fold Public API facts into the common health fields of a view.

    Computed on read rather than stored: a key revoked from Developer settings
    must show here at once, and a GET must not write.
    """
    view["last_success_at"] = data["last_api_call_at"] or view["last_success_at"]
    view["webhook_state"] = None
    if data["webhook_url"]:
        deliveries = data["deliveries"]
        failing = deliveries["failed_24h"] and deliveries["last_status"] != "DELIVERED"
        view["webhook_state"] = "FAILING" if failing else "ACTIVE"
        view["last_webhook_at"] = deliveries["last_at"]
        if failing and view["health"] in {"CONNECTED", "DEGRADED"}:
            view["health"] = "WEBHOOK_FAILING"
    if view["state"] != "DISCONNECTED" and not data["key_active"]:
        view["health"], view["last_error_code"] = "AUTH_EXPIRED", "API_KEY_REVOKED"
    return view


def package(conn: IntegrationConnection) -> dict[str, Any]:
    """The Developer view. Identifiers only; secrets were shown once at creation."""
    return {
        "api_base_url": api_base_url(),
        "orders_endpoint": f"{api_base_url()}/sources/{conn.source_id}/orders",
        "source_id": conn.source_id,
        "scopes": conn.config.get("scopes") or SCOPES,
        "topics": sorted(TOPICS),
        "signature_header": "X-Ecomsbd-Signature",
    }


async def connection_for_key(db: AsyncSession, key_id: uuid.UUID) -> IntegrationConnection | None:
    rows = (
        await db.scalars(
            sa.select(IntegrationConnection).where(
                IntegrationConnection.provider == "CUSTOM_WEBSITE",
                IntegrationConnection.state != "DISCONNECTED",
            )
        )
    ).all()
    return next((row for row in rows if row.config.get("api_key_id") == str(key_id)), None)


async def apply_status(
    db: AsyncSession,
    key_id: uuid.UUID,
    order_id: uuid.UUID,
    status: str,
    reason: str | None,
) -> dict[str, Any]:
    """A website confirms or cancels an order it sent, through the order service."""
    from app.api.deps import get_hasher, get_vault
    from app.customers.service import CustomerService
    from app.integrations.inbound import booked
    from app.orders.models import Order, OrderStatus
    from app.orders.service import OrderService

    order = await db.scalar(
        sa.select(Order).where(Order.id == order_id, Order.deleted_at.is_(None))
    )
    if order is None:
        raise NotFoundError()
    current = str(order.status)
    if current == status:
        return {"order_id": str(order.id), "status": current, "result": "UNCHANGED"}
    if status == "CANCELLED" and await booked(db, order):
        conn = await connection_for_key(db, key_id)
        conflict_id = None
        if conn is not None:
            conflict = await sync.open_conflict(
                db,
                conn,
                kind="CANCELLED_AFTER_BOOKING",
                entity="ORDER",
                fingerprint=f"cancel:{order.id}",
                order_id=order.id,
                detail={
                    "ecomsbd": {"status": current, "order_number": order.order_number},
                    "external": {"status": "CANCELLED", "reason": reason},
                },
            )
            conflict_id = str(conflict.id)
        return {
            "order_id": str(order.id),
            "status": current,
            "result": "CONFLICT",
            "code": "CANCELLED_AFTER_BOOKING",
            "conflict_id": conflict_id,
        }
    service_ = OrderService(
        db, customers=CustomerService(db, hasher=get_hasher(), vault=get_vault())
    )
    updated = await service_.transition(
        order.id,
        OrderStatus(status),
        reason=(reason or "Cancelled by the website") if status == "CANCELLED" else None,
    )
    return {"order_id": str(updated.id), "status": str(updated.status), "result": "UPDATED"}
