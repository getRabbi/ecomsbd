from __future__ import annotations

import uuid
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import DbSession, Principal, require_permission
from app.common.operation_lock import lock_shop
from app.core.clock import utc_now
from app.core.errors import ConflictError, ValidationError
from app.messaging.service import required
from app.public_api.auth import SCOPES
from app.public_api.models import ApiKey, WebhookAttempt, WebhookDelivery, WebhookEndpoint
from app.public_api.service import issue_key
from app.public_api.webhooks import TOPICS, create_endpoint, queue_test_delivery
from app.tenants.roles import Permission

router = APIRouter(prefix="/developers", tags=["developer settings"])
Manager = Annotated[Principal, Depends(require_permission(Permission.SETTINGS_MANAGE))]


class KeyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    scopes: list[str] = Field(min_length=1, max_length=9)
    rate_limit: int = Field(default=60, ge=1, le=600)


class WebhookInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=10, max_length=1000)
    topics: list[str] = Field(min_length=1, max_length=20)


class ToggleInput(BaseModel):
    enabled: bool


def key_view(row: ApiKey) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "scopes": row.scopes,
        "rate_limit": row.rate_limit,
        "revoked_at": row.revoked_at,
        "created_at": row.created_at,
    }


def hook_view(row: WebhookEndpoint) -> dict[str, Any]:
    return {"id": row.id, "url": row.url, "topics": row.topics, "enabled": row.enabled}


@router.get("/keys")
async def keys(db: DbSession, _: Manager) -> dict[str, Any]:
    return {
        "items": [
            key_view(row)
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
    )
    response.headers["Cache-Control"] = "no-store"
    return {**key_view(row), "key": token}


@router.delete("/keys/{key_id}")
async def revoke(key_id: uuid.UUID, db: DbSession, _: Manager) -> dict[str, Any]:
    await lock_shop(db)
    row = await required(db, ApiKey, key_id)
    row.revoked_at = row.revoked_at or utc_now()
    await db.flush()
    return key_view(row)


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
async def toggle(
    endpoint_id: uuid.UUID, body: ToggleInput, db: DbSession, _: Manager
) -> dict[str, Any]:
    row = await db.scalar(
        sa.select(WebhookEndpoint).where(WebhookEndpoint.id == endpoint_id).with_for_update()
    )
    if row is None:
        row = await required(db, WebhookEndpoint, endpoint_id)
    row.enabled = body.enabled
    await db.flush()
    return hook_view(row)


@router.post("/webhooks/{endpoint_id}/test", status_code=202)
async def test_hook(endpoint_id: uuid.UUID, db: DbSession, actor: Manager) -> dict[str, Any]:
    row = await required(db, WebhookEndpoint, endpoint_id)
    if not row.enabled:
        raise ConflictError("Webhook is disabled")
    delivery = await queue_test_delivery(db, row, actor.require_tenant())
    return {"delivery_id": delivery.id, "status": delivery.status}


@router.get("/deliveries")
async def deliveries(db: DbSession, _: Manager, offset: int = Query(0, ge=0)) -> dict[str, Any]:
    rows = (
        await db.scalars(
            sa.select(WebhookDelivery)
            .order_by(WebhookDelivery.created_at.desc())
            .offset(offset)
            .limit(50)
        )
    ).all()
    return {
        "items": [
            {
                "id": row.id,
                "endpoint_id": row.endpoint_id,
                "event_id": row.event_id,
                "topic": row.payload["type"],
                "status": row.status,
                "attempts": row.attempts,
                "last_status": row.last_status,
                "last_error": row.last_error,
                "created_at": row.created_at,
            }
            for row in rows
        ]
    }


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
