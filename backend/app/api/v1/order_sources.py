import uuid
from typing import Annotated, Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import DbSession, Principal, require_permission
from app.common.operation_lock import lock_shop
from app.core.errors import ConflictError
from app.messaging.service import required
from app.order_sources import service
from app.order_sources.models import ExternalOrder, OrderSource
from app.tenants.roles import Permission

router = APIRouter(prefix="/order-sources", tags=["order sources"])
Reader = Annotated[Principal, Depends(require_permission(Permission.ORDER_VIEW))]
Writer = Annotated[Principal, Depends(require_permission(Permission.ORDER_WRITE))]
Manager = Annotated[Principal, Depends(require_permission(Permission.SETTINGS_MANAGE))]


class SourceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    provider: Literal["CUSTOM_PUSH", "SHOPIFY", "WOOCOMMERCE", "MESSENGER"] = "CUSTOM_PUSH"
    enabled: bool = False
    mapping: dict[str, str] = Field(default_factory=dict, max_length=20)


class ToggleInput(BaseModel):
    enabled: bool


class IngestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    external_order_id: str = Field(min_length=1, max_length=200, pattern=r"^\S(?:.*\S)?$")
    payload: dict[str, Any]


def source_view(row):
    return {
        "id": row.id,
        "name": row.name,
        "enabled": row.enabled,
        "mapping": row.mapping,
        **service.capability(row.provider),
    }


@router.get("")
async def sources(db: DbSession, _: Reader):
    return {
        "items": [
            source_view(row)
            for row in (
                await db.scalars(sa.select(OrderSource).order_by(OrderSource.created_at))
            ).all()
        ],
        "capabilities": [service.capability(p) for p in service.PROVIDERS],
        "mapping_fields": list(service.ORDERS_TEMPLATE.required + service.ORDERS_TEMPLATE.optional),
    }


@router.post("", status_code=201)
async def create(body: SourceInput, db: DbSession, _: Manager):
    service.validate_mapping(body.mapping)
    if body.enabled and not service.capability(body.provider)["available"]:
        raise ConflictError(
            "Official source contract required",
            details={"blocker": service.capability(body.provider)["blocker"]},
        )
    row = OrderSource(**body.model_dump())
    db.add(row)
    await db.flush()
    return source_view(row)


@router.patch("/{source_id}")
async def toggle(source_id: uuid.UUID, body: ToggleInput, db: DbSession, _: Manager):
    await lock_shop(db)
    row = await required(db, OrderSource, source_id)
    if body.enabled and not service.capability(row.provider)["available"]:
        raise ConflictError(
            "Official source contract required",
            details={"blocker": service.capability(row.provider)["blocker"]},
        )
    row.enabled = body.enabled
    await db.flush()
    return source_view(row)


@router.post("/{source_id}/ingest", status_code=201)
async def ingest(source_id: uuid.UUID, body: IngestInput, db: DbSession, _: Writer):
    return await service.ingest(db, source_id, body.external_order_id, body.payload)


@router.get("/{source_id}/history")
async def history(source_id: uuid.UUID, db: DbSession, _: Reader, offset: int = Query(0, ge=0)):
    await required(db, OrderSource, source_id)
    rows = (
        await db.scalars(
            sa.select(ExternalOrder)
            .where(ExternalOrder.source_id == source_id)
            .order_by(ExternalOrder.created_at.desc())
            .offset(offset)
            .limit(50)
        )
    ).all()
    return {
        "items": [
            {
                "order_id": row.order_id,
                "external_order_id": row.external_order_id,
                "created_at": row.created_at,
            }
            for row in rows
        ]
    }
