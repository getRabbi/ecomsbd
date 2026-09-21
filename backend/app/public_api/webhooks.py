"""Signed, at-least-once webhook delivery. Consumers dedupe by event ID."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import socket
import time
import uuid
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_vault
from app.common.outbox import OutboxEvent
from app.core.clock import utc_now
from app.core.context import ActorType, RequestContext, clear_context, set_context
from app.core.errors import ValidationError
from app.db.session import session_scope, system_session
from app.public_api.models import WebhookAttempt, WebhookDelivery, WebhookEndpoint

TOPICS = frozenset(
    {
        "order.created",
        "order.status_changed",
        "order.booked",
        "consignment.status_changed",
        "import.committed",
    }
)


def valid_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        if (
            parsed.scheme != "https"
            or not host
            or parsed.port not in (None, 443)
            or parsed.username
            or parsed.password
            or parsed.fragment
            or any(c.isspace() for c in url)
        ):
            raise ValueError
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError
    except ValueError as exc:
        raise ValidationError("Webhook URL must use HTTPS on a public host, port 443") from exc
    return host


async def resolve_public(host: str) -> str:
    answers = await asyncio.wait_for(
        asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM), timeout=5
    )
    addresses = {item[4][0] for item in answers}
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValidationError("Webhook DNS must resolve only to public addresses")
    return sorted(addresses)[0]


def signature(secret: str, body: bytes, timestamp: int) -> str:
    digest = hmac.new(
        secret.encode(), str(timestamp).encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v1={digest}"


def verify_signature(secret: str, body: bytes, header: str, *, now: int | None = None) -> bool:
    try:
        stamp = int(header.split(",")[0].removeprefix("t="))
        if abs((now if now is not None else int(time.time())) - stamp) > 300:
            return False
        return hmac.compare_digest(signature(secret, body, stamp), header)
    except (ValueError, IndexError):
        return False


async def post_signed(url: str, body: bytes, headers: dict[str, str]) -> int:
    host = valid_url(url)
    ip = await resolve_public(host)
    # Pin the validated IP; TLS still verifies the original hostname. No proxy,
    # redirect, or second DNS lookup can turn the request into a private one.
    target = httpx.URL(url).copy_with(host=ip)
    async with (
        httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client,
        client.stream(
            "POST",
            target,
            content=body,
            headers={**headers, "Host": host},
            extensions={"sni_hostname": host},
        ) as response,
    ):
        return response.status_code


async def schedule_event(db: AsyncSession, event: OutboxEvent) -> None:
    if event.tenant_id is None or event.topic not in TOPICS:
        return
    endpoints = (
        await db.scalars(
            sa.select(WebhookEndpoint).where(
                WebhookEndpoint.tenant_id == event.tenant_id, WebhookEndpoint.enabled.is_(True)
            )
        )
    ).all()
    for endpoint in endpoints:
        if event.topic not in endpoint.topics:
            continue
        payload = {
            "id": str(event.id),
            "type": event.topic,
            "version": "1",
            "created_at": utc_now().isoformat(),
            "shop_id": str(event.tenant_id),
            "data": {
                key: value
                for key, value in event.payload.items()
                if key
                in {"order_id", "consignment_id", "import_id", "status", "old_status", "new_status"}
            },
        }
        db.add(
            WebhookDelivery(
                tenant_id=event.tenant_id,
                endpoint_id=endpoint.id,
                event_id=event.id,
                payload=payload,
                next_attempt_at=utc_now(),
            )
        )


async def deliver(tenant_id: uuid.UUID, delivery_id: uuid.UUID) -> None:
    token = set_context(
        RequestContext(
            trace_id=uuid.uuid4().hex,
            tenant_id=tenant_id,
            actor_type=ActorType.SYSTEM,
            job_name="webhooks",
        )
    )
    try:
        async with session_scope() as db:
            row = await db.scalar(
                sa.select(WebhookDelivery)
                .where(WebhookDelivery.id == delivery_id)
                .with_for_update()
            )
            if not row or row.status not in {"PENDING", "RETRY"} or row.next_attempt_at > utc_now():
                return
            endpoint = await db.scalar(
                sa.select(WebhookEndpoint)
                .where(WebhookEndpoint.id == row.endpoint_id)
                .with_for_update()
            )
            if endpoint is None or not endpoint.enabled:
                row.status = "DISABLED"
                return
            body = json.dumps(
                row.payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
            secret = get_vault().decrypt(endpoint.secret_enc, context=f"webhook:{endpoint.id}")
            stamp = int(time.time())
            status, error = None, None
            try:
                status = await post_signed(
                    endpoint.url,
                    body,
                    {
                        "Content-Type": "application/json",
                        "X-Ecomsbd-Signature": signature(secret, body, stamp),
                        "X-Ecomsbd-Event-Id": str(row.event_id),
                        "X-Ecomsbd-Delivery-Id": str(row.id),
                    },
                )
            except (httpx.HTTPError, OSError, TimeoutError, ValidationError):
                error = "DESTINATION_UNAVAILABLE_OR_UNSAFE"
            row.attempts += 1
            row.last_status, row.last_error = status, error
            row.status = (
                "DELIVERED"
                if status and 200 <= status < 300
                else ("FAILED" if row.attempts >= 6 else "RETRY")
            )
            row.next_attempt_at = utc_now() + timedelta(seconds=min(3600, 30 * 2**row.attempts))
            db.add(WebhookAttempt(delivery_id=row.id, status_code=status, error=error))
    finally:
        clear_context(token)


async def dispatch_webhooks(ctx: dict[str, Any] | None = None) -> dict[str, int]:
    async with system_session("outbound webhooks: due IDs") as db:
        rows = (
            await db.execute(
                sa.select(WebhookDelivery.tenant_id, WebhookDelivery.id)
                .where(
                    WebhookDelivery.status.in_(["PENDING", "RETRY"]),
                    WebhookDelivery.next_attempt_at <= utc_now(),
                )
                .order_by(WebhookDelivery.next_attempt_at)
                .limit(25)
            )
        ).all()
    for tenant_id, delivery_id in rows:
        await deliver(tenant_id, delivery_id)
    return {"processed": len(rows)}
