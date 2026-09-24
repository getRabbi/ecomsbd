"""Unauthenticated provider traffic: OAuth callbacks and webhooks.

The order of every handler is the security design, as in the courier
webhooks: resolve the connection by an unguessable token across shops, become
that shop, verify the provider's signature with that connection's secret, and
only then read the body as anything but bytes.

Webhooks are durable-first. A verified delivery becomes one ``QUEUED`` event
keyed by the provider's delivery id (a retry is a no-op) and the worker imports
the order by reading it back from the provider's API.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utc_now
from app.core.context import ActorType, use_context
from app.core.errors import AppError, ValidationError
from app.integrations import meta, service, shopify, woocommerce
from app.integrations.http import ProviderError
from app.integrations.models import IntegrationConnection, IntegrationEvent

MAX_BODY = 1024 * 1024
ORDER_TOPICS = frozenset({"orders/create", "orders/updated", "order.created", "order.updated"})


@dataclass
class Ack:
    status: int
    body: dict[str, Any] = field(default_factory=lambda: {"received": True})


@asynccontextmanager
async def _as_shop(db: AsyncSession, conn: IntegrationConnection) -> AsyncIterator[None]:
    """Become the connection's shop, and flush before leaving it.

    The request session commits after the handler returns, outside this scope,
    where the tenancy guard would refuse any write still pending.
    """
    with use_context(
        tenant_id=conn.tenant_id,
        actor_type=ActorType.PROVIDER,
        user_id=None,
        job_name=f"integration:{conn.provider.lower()}",
    ):
        yield
        await db.flush()


# ------------------------------------------------------------- callbacks ---


async def shopify_callback(
    db: AsyncSession, params: list[tuple[str, str]]
) -> tuple[uuid.UUID | None, str]:
    values = dict(params)
    conn = await service.by_state(db, "SHOPIFY", values.get("state", ""))
    if conn is None:
        return None, "STATE_INVALID"
    async with _as_shop(db, conn):
        conn.state_hash = conn.state_expires_at = None  # single use, success or not
        if not shopify.configured() or not shopify.valid_callback(params):
            await service.record_issue(
                db, conn, kind="SECURITY", topic="oauth.callback", code="SIGNATURE_INVALID"
            )
            return conn.id, "SIGNATURE_INVALID"
        try:
            shop = shopify.normalize_shop(values.get("shop", ""))
        except ValidationError:
            return conn.id, "ACCOUNT_MISMATCH"
        if shop != conn.account_id or not values.get("code"):
            return conn.id, "ACCOUNT_MISMATCH"
        try:
            await service.complete_shopify(db, conn, shop, values["code"])
        except ProviderError as exc:
            service.mark_error(conn, exc.code)
            return conn.id, exc.code
        except AppError as exc:
            return conn.id, (exc.details or {}).get("code", "ALREADY_LINKED")
    return conn.id, "CONNECTED"


async def woocommerce_callback(db: AsyncSession, body: bytes) -> int:
    """WooCommerce POSTs the generated keys here; ``user_id`` is our state."""
    try:
        data = json.loads(body)
    except ValueError:
        return 400
    if not isinstance(data, dict):
        return 400
    conn = await service.by_state(db, "WOOCOMMERCE", str(data.get("user_id") or ""))
    key, secret = data.get("consumer_key"), data.get("consumer_secret")
    if (
        conn is None
        or not isinstance(key, str)
        or not isinstance(secret, str)
        or not key.startswith("ck_")
        or not secret.startswith("cs_")
        or max(len(key), len(secret)) > 100
        or data.get("key_permissions") != "read_write"
    ):
        return 400
    async with _as_shop(db, conn):
        conn.state_hash = conn.state_expires_at = None
        service.seal(conn, {"consumer_key": key, "consumer_secret": secret})
        # Verified against the store when the seller lands back in ecomsbd; a
        # request back into the store from inside its own callback can stall
        # small hosts.
        conn.config = {**conn.config, "keys_received_at": utc_now().isoformat()}
    return 200


async def meta_callback(db: AsyncSession, params: dict[str, str]) -> tuple[uuid.UUID | None, str]:
    conn = await service.by_state(db, "MESSENGER", params.get("state", ""))
    if conn is None:
        return None, "STATE_INVALID"
    async with _as_shop(db, conn):
        conn.state_hash = conn.state_expires_at = None
        if params.get("error") or not params.get("code"):
            return conn.id, "ACCESS_DENIED"
        try:
            pages = await meta.pages(await meta.exchange_code(params["code"]))
        except ProviderError as exc:
            service.mark_error(conn, exc.code)
            return conn.id, exc.code
        if not pages:
            return conn.id, "NO_PAGES"
        service.seal(conn, {"pages": {page["id"]: page["token"] for page in pages}})
        conn.config = {
            **conn.config,
            "pages": [{"id": page["id"], "name": page["name"]} for page in pages],
        }
        if len(pages) != 1:
            return conn.id, "SELECT_PAGE"
        try:
            await select_page(db, conn, pages[0]["id"])
        except ProviderError as exc:
            service.mark_error(conn, exc.code)
            return conn.id, exc.code
        except AppError as exc:
            return conn.id, (exc.details or {}).get("code", "ALREADY_LINKED")
    return conn.id, "CONNECTED"


async def select_page(db: AsyncSession, conn: IntegrationConnection, page_id: str) -> None:
    token = (service.unseal(conn).get("pages") or {}).get(page_id)
    if not token:
        raise ValidationError(
            "Choose one of the Pages you allowed", details={"code": "PAGE_UNKNOWN"}
        )
    await service.ensure_unlinked(db, conn, page_id)
    await meta.subscribe(page_id, token)
    info = await meta.check(page_id, token)
    await service.link(db, conn, page_id, info.get("name"))
    service.seal(conn, {"page_token": token})
    conn.config = {k: v for k, v in conn.config.items() if k != "pages"}
    service.mark_ok(conn)
    await service.went_live(db, conn)


# -------------------------------------------------------------- webhooks ---


def _delivery_id(headers: dict[str, str], body: bytes) -> str:
    value = headers.get("x-shopify-webhook-id") or headers.get("x-wc-webhook-delivery-id")
    return (value or "sha256:" + hashlib.sha256(body).hexdigest())[:200]


async def receive(
    db: AsyncSession, provider: str, token: str, headers: dict[str, str], body: bytes
) -> Ack:
    conn = await service.by_token(db, provider, token)
    if conn is None or conn.state in {"PENDING", "DISCONNECTED"}:
        # The same answer as a processed delivery: a callback URL must not
        # become an oracle for which shops exist.
        return Ack(200)
    async with _as_shop(db, conn):
        if provider == "WOOCOMMERCE" and woocommerce.is_ping(body):
            return Ack(200)
        if provider == "SHOPIFY":
            valid = shopify.valid_webhook(body, headers.get("x-shopify-hmac-sha256"))
            # One store per connection: a body signed for another store is refused.
            valid = valid and headers.get("x-shopify-shop-domain", "").lower() == conn.account_id
            topic = headers.get("x-shopify-topic", "")
        else:
            valid = woocommerce.valid_webhook(
                service.webhook_secret(conn), body, headers.get("x-wc-webhook-signature")
            )
            topic = headers.get("x-wc-webhook-topic", "")
        if not valid:
            await service.record_issue(
                db, conn, kind="SECURITY", topic="webhook.signature", code="SIGNATURE_INVALID"
            )
            return Ack(401, {"received": False})
        service.mark_ok(conn, webhook=True)
        if topic == "app/uninstalled":
            service.seal(conn, None)
            conn.state = "AUTH_EXPIRED"
            service.mark_error(conn, "APP_UNINSTALLED")
            await service.record_issue(
                db, conn, kind="AUTH", topic="app.uninstalled", code="APP_UNINSTALLED"
            )
            return Ack(200)
        if topic not in ORDER_TOPICS:
            return Ack(200)
        try:
            payload = json.loads(body)
            ref, hint = service.CONNECTORS[provider].webhook_order(
                payload if isinstance(payload, dict) else {}
            )
        except (ValueError, ProviderError):
            await service.record_issue(
                db, conn, kind="WEBHOOK", topic=topic, code="MALFORMED_PAYLOAD"
            )
            return Ack(200)
        pending = await db.scalar(
            sa.select(IntegrationEvent.id).where(
                IntegrationEvent.connection_id == conn.id,
                IntegrationEvent.external_ref == ref,
                IntegrationEvent.status == "QUEUED",
            )
        )
        event = IntegrationEvent(
            connection_id=conn.id,
            provider=provider,
            kind="WEBHOOK",
            topic=topic[:64],
            delivery_id=_delivery_id(headers, body),
            external_ref=ref,
            hint=hint,
            # A queued read of the same order already covers this delivery.
            status="IGNORED" if pending and not hint else "QUEUED",
            code="COALESCED" if pending and not hint else None,
            next_attempt_at=utc_now(),
        )
        try:
            async with db.begin_nested():
                db.add(event)
                await db.flush()
        except IntegrityError:
            return Ack(200, {"received": True, "duplicate": True})
    return Ack(200)


async def shopify_compliance(db: AsyncSession, headers: dict[str, str], body: bytes) -> Ack:
    """Shopify's mandatory privacy webhooks, configured on the app, not per shop."""
    if not shopify.valid_webhook(body, headers.get("x-shopify-hmac-sha256")):
        return Ack(401, {"received": False})
    topic = headers.get("x-shopify-topic", "")
    shop = headers.get("x-shopify-shop-domain", "").lower()
    for conn in await service.by_account(db, "SHOPIFY", shop):
        async with _as_shop(db, conn):
            if topic == "shop/redact":
                await service.disconnect(db, conn, remote=False)
            elif topic in {"customers/data_request", "customers/redact"}:
                # ecomsbd holds the seller's own order records; the seller
                # decides what to hand over or delete, so they are told.
                await service.record_issue(
                    db,
                    conn,
                    kind="SECURITY",
                    topic=topic,
                    code="CUSTOMER_DATA_REQUEST"
                    if topic == "customers/data_request"
                    else "CUSTOMER_REDACT_REQUEST",
                )
    return Ack(200)


async def meta_event(db: AsyncSession, headers: dict[str, str], body: bytes) -> Ack:
    """Page events refresh connection health only; message bodies are dropped."""
    if not meta.valid_webhook(body, headers.get("x-hub-signature-256")):
        return Ack(401, {"received": False})
    try:
        payload = json.loads(body)
    except ValueError:
        return Ack(200)
    if not isinstance(payload, dict) or payload.get("object") != "page":
        return Ack(200)
    page_ids = {
        str(entry.get("id")) for entry in payload.get("entry") or [] if isinstance(entry, dict)
    }
    for page_id in sorted(page_ids)[:20]:
        for conn in await service.by_account(db, "MESSENGER", page_id):
            async with _as_shop(db, conn):
                service.mark_ok(conn, webhook=True)
    return Ack(200)
