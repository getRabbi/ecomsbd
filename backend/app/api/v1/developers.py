"""Developer portal (V2 developer settings, extended in V3.8).

Everything here manages the existing Public API keys and signed webhooks. A
full API key or signing secret is returned once, when it is created or rotated,
with ``Cache-Control: no-store``; it is never readable again.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Annotated, Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import DbSession, Principal, get_hasher, get_rate_limiter, require_permission
from app.api.v1.integrations import connection_views
from app.common.audit import AuditAction, record_audit
from app.common.cache import RateLimiter
from app.common.operation_lock import lock_shop
from app.core.clock import utc_now
from app.core.errors import ConflictError, RateLimitedError, ValidationError
from app.integrations import custom_website
from app.integrations.models import IntegrationConnection
from app.messaging.service import required
from app.public_api.auth import SCOPES
from app.public_api.models import (
    ApiKey,
    ApiWriteReceipt,
    WebhookAttempt,
    WebhookDelivery,
    WebhookEndpoint,
)
from app.public_api.service import issue_key, key_state, revoke_key, rotate_key
from app.public_api.webhooks import (
    TOPICS,
    create_endpoint,
    queue_test_delivery,
    rotate_secret,
    sample_event,
    signature,
    update_endpoint,
)
from app.tenants.roles import Permission

router = APIRouter(prefix="/developers", tags=["developer settings"])
Manager = Annotated[Principal, Depends(require_permission(Permission.SETTINGS_MANAGE))]
Limiter = Annotated[RateLimiter, Depends(get_rate_limiter)]
#: Placeholder signing secret for the sample viewer. Not a real secret.
EXAMPLE_SECRET = "example-signing-secret"  # noqa: S105 - a documented placeholder


class KeyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    scopes: list[str] = Field(min_length=1, max_length=9)
    rate_limit: int = Field(default=60, ge=1, le=600)
    #: Optional expiry, in days from now.
    expires_in_days: int | None = Field(default=None, ge=1, le=730)


class WebhookInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=10, max_length=1000)
    topics: list[str] = Field(min_length=1, max_length=20)


class WebhookUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    topics: list[str] | None = Field(default=None, min_length=1, max_length=20)


class KeyCheckInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=10, max_length=200)


def key_view(row: ApiKey, connections: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "scopes": row.scopes,
        "rate_limit": row.rate_limit,
        "state": key_state(row),
        "revoked_at": row.revoked_at,
        "expires_at": row.expires_at,
        "last_used_at": row.last_used_at,
        "created_at": row.created_at,
        #: The Custom Website connection that uses this key, if one does.
        "connection_id": (connections or {}).get(str(row.id)),
    }


def hook_view(row: WebhookEndpoint) -> dict[str, Any]:
    return {
        "id": row.id,
        "url": row.url,
        "topics": row.topics,
        "enabled": row.enabled,
        "created_at": row.created_at,
    }


def delivery_view(row: WebhookDelivery) -> dict[str, Any]:
    return {
        "id": row.id,
        "endpoint_id": row.endpoint_id,
        "event_id": row.event_id,
        "topic": row.payload["type"],
        "status": row.status,
        "attempts": row.attempts,
        "last_status": row.last_status,
        "last_error": row.last_error,
        "next_attempt_at": row.next_attempt_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


async def _key_connections(db: DbSession) -> dict[str, str]:
    rows = (
        await db.scalars(
            sa.select(IntegrationConnection).where(
                IntegrationConnection.provider == "CUSTOM_WEBSITE",
                IntegrationConnection.state != "DISCONNECTED",
            )
        )
    ).all()
    return {
        str(row.config.get("api_key_id")): str(row.id)
        for row in rows
        if row.config.get("api_key_id")
    }


# ------------------------------------------------------------------ keys ---


@router.get("/keys")
async def keys(db: DbSession, _: Manager) -> dict[str, Any]:
    connections = await _key_connections(db)
    return {
        "items": [
            key_view(row, connections)
            for row in (
                await db.scalars(sa.select(ApiKey).order_by(ApiKey.created_at.desc()))
            ).all()
        ],
        "scopes": sorted(SCOPES),
    }


@router.post("/keys", status_code=201)
async def create_key(
    body: KeyInput, db: DbSession, actor: Manager, response: Response
) -> dict[str, Any]:
    if set(body.scopes) - SCOPES:
        raise ValidationError("Unknown API scope")
    row, token = await issue_key(
        db,
        name=body.name,
        scopes=body.scopes,
        rate_limit=body.rate_limit,
        created_by=actor.user_id,
        expires_at=(
            utc_now() + timedelta(days=body.expires_in_days) if body.expires_in_days else None
        ),
    )
    response.headers["Cache-Control"] = "no-store"
    return {**key_view(row), "key": token}


@router.post("/keys/{key_id}/rotate", status_code=201)
async def rotate(
    key_id: uuid.UUID, db: DbSession, actor: Manager, response: Response
) -> dict[str, Any]:
    """A new secret for the same key settings. The old key stops working now."""
    await lock_shop(db)
    old = await required(db, ApiKey, key_id)
    if key_state(old) != "ACTIVE":
        raise ConflictError("Only an active key can be rotated")
    row, token = await rotate_key(db, old, actor.user_id)
    # A Custom Website connection follows its key, so its health stays true.
    for conn in (
        await db.scalars(
            sa.select(IntegrationConnection).where(
                IntegrationConnection.provider == "CUSTOM_WEBSITE"
            )
        )
    ).all():
        if conn.config.get("api_key_id") == str(old.id):
            conn.config = {**conn.config, "api_key_id": str(row.id)}
    await db.flush()
    response.headers["Cache-Control"] = "no-store"
    return {**key_view(row, await _key_connections(db)), "key": token, "replaces": old.id}


@router.delete("/keys/{key_id}")
async def revoke(key_id: uuid.UUID, db: DbSession, _: Manager) -> dict[str, Any]:
    await lock_shop(db)
    row = await required(db, ApiKey, key_id)
    await revoke_key(db, row)
    return key_view(row, await _key_connections(db))


@router.post("/keys/check")
async def check_key(
    body: KeyCheckInput, db: DbSession, actor: Manager, limiter: Limiter
) -> dict[str, Any]:
    """Test API key: whether a pasted key belongs to this shop and would work.

    The key is compared by hash and never stored, logged or echoed back.
    """
    result = await limiter.hit(
        "developer-key-check", str(actor.require_tenant()), limit=30, window_seconds=3600
    )
    if not result.allowed:
        raise RateLimitedError(retry_after_seconds=result.retry_after_seconds)
    token = body.key.strip()
    row: ApiKey | None = None
    try:
        identity = uuid.UUID(hex=token.removeprefix("ec_live_").split(".", 1)[0])
        if token.startswith("ec_live_"):
            row = await db.scalar(sa.select(ApiKey).where(ApiKey.id == identity))
    except ValueError:
        row = None
    if row is None or not get_hasher().verify_token("public:" + token, row.secret_hash):
        return {"valid": False, "state": "UNKNOWN"}
    state = key_state(row)
    return {
        "valid": state == "ACTIVE",
        "state": state,
        "key_id": row.id,
        "name": row.name,
        "scopes": row.scopes,
        "rate_limit": row.rate_limit,
        "expires_at": row.expires_at,
        "last_used_at": row.last_used_at,
    }


@router.get("/requests")
async def request_history(
    db: DbSession,
    _: Manager,
    key_id: uuid.UUID | None = None,
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Recent write requests, from the idempotency receipts. No request bodies."""
    query = sa.select(ApiWriteReceipt).order_by(ApiWriteReceipt.created_at.desc())
    if key_id is not None:
        query = query.where(ApiWriteReceipt.key_id == key_id)
    rows = (await db.scalars(query.offset(offset).limit(50))).all()
    return {
        "items": [
            {
                "id": row.id,
                "key_id": row.key_id,
                "endpoint": row.endpoint,
                "idempotency_key": row.idempotency_key,
                "created_at": row.created_at,
            }
            for row in rows
        ],
        "next_offset": offset + len(rows),
    }


# -------------------------------------------------------------- webhooks ---


@router.get("/webhooks")
async def webhooks(db: DbSession, _: Manager) -> dict[str, Any]:
    return {
        "items": [
            hook_view(row)
            for row in (
                await db.scalars(
                    sa.select(WebhookEndpoint).order_by(WebhookEndpoint.created_at.desc())
                )
            ).all()
        ],
        "topics": sorted(TOPICS),
    }


@router.post("/webhooks", status_code=201)
async def create_webhook(
    body: WebhookInput, db: DbSession, _: Manager, response: Response
) -> dict[str, Any]:
    row, secret = await create_endpoint(db, body.url, body.topics)
    response.headers["Cache-Control"] = "no-store"
    return {**hook_view(row), "signing_secret": secret}


@router.patch("/webhooks/{endpoint_id}")
async def update_webhook(
    endpoint_id: uuid.UUID, body: WebhookUpdate, db: DbSession, _: Manager
) -> dict[str, Any]:
    row = await db.scalar(
        sa.select(WebhookEndpoint).where(WebhookEndpoint.id == endpoint_id).with_for_update()
    )
    if row is None:
        row = await required(db, WebhookEndpoint, endpoint_id)
    await update_endpoint(db, row, enabled=body.enabled, topics=body.topics)
    return hook_view(row)


@router.post("/webhooks/{endpoint_id}/rotate-secret", status_code=201)
async def rotate_webhook_secret(
    endpoint_id: uuid.UUID, db: DbSession, _: Manager, response: Response
) -> dict[str, Any]:
    """A new signing secret. Deliveries from now on are signed with it only."""
    row = await db.scalar(
        sa.select(WebhookEndpoint).where(WebhookEndpoint.id == endpoint_id).with_for_update()
    )
    if row is None:
        row = await required(db, WebhookEndpoint, endpoint_id)
    secret = await rotate_secret(db, row)
    response.headers["Cache-Control"] = "no-store"
    return {**hook_view(row), "signing_secret": secret}


@router.post("/webhooks/{endpoint_id}/test", status_code=202)
async def test_hook(endpoint_id: uuid.UUID, db: DbSession, actor: Manager) -> dict[str, Any]:
    """Queue a ``webhook.test`` event. It creates no business data."""
    row = await required(db, WebhookEndpoint, endpoint_id)
    if not row.enabled:
        raise ConflictError("Webhook is disabled")
    delivery = await queue_test_delivery(db, row, actor.require_tenant())
    return {"delivery_id": delivery.id, "status": delivery.status, "creates_data": False}


@router.get("/deliveries")
async def deliveries(
    db: DbSession,
    _: Manager,
    offset: int = Query(0, ge=0),
    status: Literal["PENDING", "RETRY", "DELIVERED", "FAILED", "DISABLED"] | None = None,
    endpoint_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    query = sa.select(WebhookDelivery).order_by(WebhookDelivery.created_at.desc())
    if status is not None:
        query = query.where(WebhookDelivery.status == status)
    if endpoint_id is not None:
        query = query.where(WebhookDelivery.endpoint_id == endpoint_id)
    rows = (await db.scalars(query.offset(offset).limit(50))).all()
    return {"items": [delivery_view(row) for row in rows], "next_offset": offset + len(rows)}


@router.post("/deliveries/{delivery_id}/retry")
async def retry(delivery_id: uuid.UUID, db: DbSession, _: Manager) -> dict[str, Any]:
    row = await db.scalar(
        sa.select(WebhookDelivery).where(WebhookDelivery.id == delivery_id).with_for_update()
    )
    if row is None:
        row = await required(db, WebhookDelivery, delivery_id)
    if row.status not in {"FAILED", "DISABLED"}:
        raise ConflictError("Only failed or disabled deliveries can be retried")
    endpoint = await required(db, WebhookEndpoint, row.endpoint_id)
    if not endpoint.enabled:
        raise ConflictError("Enable the webhook before retrying")
    row.status, row.attempts, row.next_attempt_at = "PENDING", 0, utc_now()
    await db.flush()
    await record_audit(
        db,
        AuditAction.WEBHOOK_DELIVERY_RETRIED,
        entity_type="webhook_delivery",
        entity_id=row.id,
    )
    return {"id": row.id, "status": row.status}


@router.get("/deliveries/{delivery_id}/attempts")
async def attempts(delivery_id: uuid.UUID, db: DbSession, _: Manager) -> dict[str, Any]:
    await required(db, WebhookDelivery, delivery_id)
    rows = (
        await db.scalars(
            sa.select(WebhookAttempt)
            .where(WebhookAttempt.delivery_id == delivery_id)
            .order_by(WebhookAttempt.created_at.desc())
            .limit(50)
        )
    ).all()
    return {
        "items": [
            {
                "id": row.id,
                "status_code": row.status_code,
                "error": row.error,
                "created_at": row.created_at,
            }
            for row in rows
        ]
    }


# ---------------------------------------------------------- health, tools ---


@router.get("/health")
async def health(db: DbSession, _: Manager) -> dict[str, Any]:
    """API and webhook health, from the same facts the Integrations Hub reads."""
    now = utc_now()
    key_rows = (await db.scalars(sa.select(ApiKey))).all()
    active = [row for row in key_rows if key_state(row, now) == "ACTIVE"]
    last_call = max((row.last_used_at for row in active if row.last_used_at), default=None)
    soon = [
        row.id
        for row in active
        if row.expires_at is not None and row.expires_at - now < timedelta(days=7)
    ]
    endpoints = []
    for endpoint in (await db.scalars(sa.select(WebhookEndpoint))).all():
        latest = await db.scalar(
            sa.select(WebhookDelivery)
            .where(WebhookDelivery.endpoint_id == endpoint.id)
            .order_by(WebhookDelivery.created_at.desc())
            .limit(1)
        )
        failing = await db.scalar(
            sa.select(sa.func.count())
            .select_from(WebhookDelivery)
            .where(
                WebhookDelivery.endpoint_id == endpoint.id,
                WebhookDelivery.status.in_(["FAILED", "RETRY"]),
                WebhookDelivery.created_at >= now - timedelta(days=1),
            )
        )
        error = await db.scalar(
            sa.select(WebhookDelivery)
            .where(
                WebhookDelivery.endpoint_id == endpoint.id,
                WebhookDelivery.last_error.is_not(None) | (WebhookDelivery.status == "FAILED"),
            )
            .order_by(WebhookDelivery.updated_at.desc())
            .limit(1)
        )
        failed = int(failing or 0)
        endpoints.append(
            {
                **hook_view(endpoint),
                "state": (
                    "DISABLED"
                    if not endpoint.enabled
                    else "FAILING"
                    if failed and (latest is None or latest.status != "DELIVERED")
                    else "ACTIVE"
                ),
                "failed_24h": failed,
                "latest_delivery": delivery_view(latest) if latest else None,
                "latest_error": (
                    {
                        "status": error.status,
                        "last_status": error.last_status,
                        "last_error": error.last_error,
                        "at": error.updated_at,
                    }
                    if error
                    else None
                ),
            }
        )
    websites = list(
        (
            await db.scalars(
                sa.select(IntegrationConnection)
                .where(IntegrationConnection.provider == "CUSTOM_WEBSITE")
                .order_by(IntegrationConnection.created_at)
            )
        ).all()
    )
    return {
        "api": {
            "state": "ACTIVE" if active else "NO_ACTIVE_KEY",
            "active_keys": len(active),
            "last_request_at": last_call,
            "expiring_soon": soon,
        },
        "webhooks": endpoints,
        # The Integrations Hub's own view of each Custom Website: one health truth.
        "connections": await connection_views(db, websites, True),
    }


@router.get("/samples")
async def samples(_: Manager) -> dict[str, Any]:
    """Sample payloads for the viewer. Placeholders only; nothing is sent or created."""
    order = {
        "external_order_id": "WEB-1001",
        "payload": {
            "phone": "01700000000",
            "customer_name": "Sample Customer",
            "address": "House 1, Road 2",
            "district": "Dhaka",
            "items": [{"name": "Sample product", "quantity": 1, "unit_price_paisa": 120000}],
            "cod_amount_paisa": 120000,
        },
    }
    events = {topic: sample_event(topic) for topic in sorted(TOPICS)}
    example = events["order.confirmed"]
    body = json.dumps(example, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {
        "base_path": "/public/v1",
        "order": order,
        "events": events,
        "signature_example": {
            "secret": EXAMPLE_SECRET,
            "timestamp": 1767225600,
            "body": body,
            "header": signature(EXAMPLE_SECRET, body.encode(), 1767225600),
        },
        "headers": ["X-Ecomsbd-Signature", "X-Ecomsbd-Event-Id", "X-Ecomsbd-Delivery-Id"],
        "tolerance_seconds": 300,
        "scopes": sorted(SCOPES),
        "custom_website_scopes": custom_website.SCOPES,
    }
