"""Two-way sync API: settings, mapping and conflicts (V3.2).

Registered before the V3.1 integrations router so ``/integrations/conflicts``
is never read as a connection id. Owners change settings, mappings and
product or stock conflicts; order writers settle order conflicts; anyone who
sees orders sees state. Nothing here returns a credential.
"""

from __future__ import annotations

import uuid
from contextlib import suppress
from typing import Annotated, Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import DbSession, Principal, require_permission
from app.common.audit import AuditAction
from app.common.operation_lock import lock_shop
from app.core.clock import utc_now
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.integrations import catalog, inbound, inventory, service, shopify, shopify_sync, sync
from app.integrations.http import ProviderError
from app.integrations.sync_models import IntegrationConflict, IntegrationLink
from app.tenants.roles import Permission

router = APIRouter(prefix="/integrations", tags=["integrations sync"])
Viewer = Annotated[Principal, Depends(require_permission(Permission.ORDER_VIEW))]
Owner = Annotated[Principal, Depends(require_permission(Permission.SETTINGS_MANAGE))]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SyncInput(Input):
    catalog: bool | None = None
    products: Literal["MANUAL", "EXTERNAL", "ECOMSBD"] | None = None
    inventory: Literal["NONE", "ECOMSBD", "EXTERNAL"] | None = None
    order_status: Literal["OFF", "TWO_WAY"] | None = None
    fulfillment: Literal["OFF", "ON"] | None = None
    location_id: str | None = Field(default=None, pattern=r"^[0-9]{1,30}$")


class MappingInput(Input):
    product_id: uuid.UUID | None = None
    variant_id: uuid.UUID | None = None
    ignore: bool = False


class ResolveInput(Input):
    resolution: str = Field(min_length=3, max_length=40)


def _sync_view(conn: Any, counts: dict[str, int], open_conflicts: int) -> dict[str, Any]:
    return {
        "settings": sync.settings_of(conn),
        "capabilities": sync.capabilities(conn),
        "status_map": sync.STATUS_MAP.get(conn.provider, {}),
        "links": counts,
        "open_conflicts": open_conflicts,
        "catalog_synced_at": conn.config.get("catalog_synced_at"),
        "inventory_synced_at": conn.config.get("inventory_synced_at"),
        "reconnect_scopes": sorted(
            {
                scope
                for feature in sync.features(sync.settings_of(conn))
                for scope in (
                    shopify_sync_missing(conn, feature) if conn.provider == "SHOPIFY" else []
                )
            }
        ),
    }


def shopify_sync_missing(conn: Any, feature: str) -> list[str]:
    return shopify.missing_scopes(conn.config.get("scopes"), feature)


async def _counts(db: DbSession, connection_id: uuid.UUID) -> tuple[dict[str, int], int]:
    rows = await db.execute(
        sa.select(IntegrationLink.state, sa.func.count())
        .where(IntegrationLink.connection_id == connection_id)
        .group_by(IntegrationLink.state)
    )
    counts: dict[str, int] = dict(rows.tuples().all())
    conflicts = await db.scalar(
        sa.select(sa.func.count())
        .select_from(IntegrationConflict)
        .where(
            IntegrationConflict.connection_id == connection_id,
            IntegrationConflict.status == "OPEN",
        )
    )
    return counts, conflicts or 0


# --------------------------------------------------------------- conflicts ---


@router.get("/conflicts")
async def conflicts(
    db: DbSession,
    _: Viewer,
    status: Literal["open", "closed"] = "open",
    connection_id: uuid.UUID | None = None,
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    query = sa.select(IntegrationConflict).where(
        IntegrationConflict.status == "OPEN"
        if status == "open"
        else IntegrationConflict.status != "OPEN"
    )
    if connection_id is not None:
        query = query.where(IntegrationConflict.connection_id == connection_id)
    rows = (
        await db.scalars(
            query.order_by(IntegrationConflict.updated_at.desc(), IntegrationConflict.id)
            .offset(offset)
            .limit(50)
        )
    ).all()
    return {"items": [sync.conflict_view(row) for row in rows], "next_offset": offset + len(rows)}


@router.post("/conflicts/{conflict_id}/resolve")
async def resolve(
    conflict_id: uuid.UUID, body: ResolveInput, db: DbSession, actor: Viewer
) -> dict[str, Any]:
    await lock_shop(db)
    conflict = await db.scalar(
        sa.select(IntegrationConflict)
        .where(IntegrationConflict.id == conflict_id)
        .with_for_update()
    )
    if conflict is None:
        raise NotFoundError()
    needed = Permission.ORDER_WRITE if conflict.entity == "ORDER" else Permission.SETTINGS_MANAGE
    if not actor.can(needed):
        raise ForbiddenError(f"Your role does not allow {needed}")
    if body.resolution not in conflict.options:
        raise ValidationError(
            "Choose one of the offered actions", details={"code": "UNKNOWN_ACTION"}
        )
    conn = await service.get(db, conflict.connection_id)
    link = await db.get(IntegrationLink, conflict.link_id) if conflict.link_id else None
    sync.close(conflict, body.resolution, actor.user_id)
    await db.flush()  # no autoflush: what follows must see it closed
    if conflict.kind in sync.STOCK_CONFLICTS and link is not None:
        await inventory.resolve_stock(db, conn, link, body.resolution)
    elif body.resolution == "IGNORE_ITEM" and link is not None:
        await catalog.set_mapping(db, conn, link, product_id=None, variant_id=None, ignore=True)
    elif body.resolution == "UNMAP" and link is not None:
        link.product_id = link.variant_id = link.internal_key = None
        link.match_source = "MANUAL"
    elif body.resolution == "MARK_REVIEWED" and conflict.kind == "ORDER_CHANGED_EXTERNALLY":
        await inbound.mark_reviewed(db, conflict.detail, conflict.order_id, conn.id)
    await service.audit(
        db,
        AuditAction.INTEGRATION_CONFIGURED,
        conn,
        change="conflict_resolved",
        kind=conflict.kind,
        resolution=body.resolution,
    )
    await db.flush()
    return sync.conflict_view(conflict)


# ---------------------------------------------------------------- settings ---


@router.get("/{connection_id}/sync-settings")
async def sync_settings(connection_id: uuid.UUID, db: DbSession, _: Viewer) -> dict[str, Any]:
    conn = await service.get(db, connection_id)
    counts, open_conflicts = await _counts(db, conn.id)
    return _sync_view(conn, counts, open_conflicts)


@router.put("/{connection_id}/sync-settings")
async def update_sync_settings(
    connection_id: uuid.UUID, body: SyncInput, db: DbSession, actor: Owner
) -> dict[str, Any]:
    conn = await service.get(db, connection_id, lock=True)
    before = sync.settings_of(conn)
    after = sync.validate(conn, body.model_dump())
    conn.config = {**conn.config, "sync": after}
    if after["catalog"] and not before["catalog"] and sync.can(conn, "catalog"):
        with suppress(ConflictError):  # a scan is already running
            await service.start_sync(
                db, conn, kind="CATALOG", since=utc_now(), until=utc_now(), actor_id=actor.user_id
            )
        conn.config = {**conn.config, "catalog_queued_at": utc_now().isoformat()}
    await service.audit(
        db,
        AuditAction.INTEGRATION_CONFIGURED,
        conn,
        change="sync_settings",
        settings={
            k: after[k] for k in ("catalog", "products", "inventory", "order_status", "fulfillment")
        },
    )
    await db.flush()
    counts, open_conflicts = await _counts(db, conn.id)
    return _sync_view(conn, counts, open_conflicts)


@router.get("/{connection_id}/locations")
async def locations(connection_id: uuid.UUID, db: DbSession, _: Owner) -> dict[str, Any]:
    conn = await service.get(db, connection_id)
    if conn.provider != "SHOPIFY":
        return {"items": []}
    if shopify_sync_missing(conn, "inventory"):
        raise ConflictError(
            "Reconnect Shopify to allow stock sync", details={"code": "RECONNECT_FOR_PERMISSION"}
        )
    credentials = service.unseal(conn)
    if not credentials:
        raise ConflictError("Reconnect the store first", details={"code": "RECONNECT_REQUIRED"})
    try:
        return {"items": await shopify_sync.locations(conn.account_id or "", credentials)}
    except ProviderError as exc:
        raise ConflictError("The store did not answer", details={"code": exc.code}) from exc


@router.post("/{connection_id}/catalog/sync", status_code=201)
async def catalog_sync(connection_id: uuid.UUID, db: DbSession, actor: Owner) -> dict[str, Any]:
    conn = await service.get(db, connection_id, lock=True)
    if not sync.can(conn, "catalog"):
        cap = sync.capabilities(conn)["catalog"]
        raise ConflictError(
            "Turn on product mapping first",
            details={"code": cap["blocker"] or "CATALOG_OFF"},
        )
    run = await service.start_sync(
        db, conn, kind="CATALOG", since=utc_now(), until=utc_now(), actor_id=actor.user_id
    )
    await db.flush()
    return service.run_view(run)


# ----------------------------------------------------------------- mapping ---


@router.get("/{connection_id}/links")
async def links(
    connection_id: uuid.UUID,
    db: DbSession,
    _: Viewer,
    state: Literal["MATCHED", "UNMATCHED", "CONFLICT", "IGNORED", "DELETED"] | None = None,
    q: str | None = Query(None, max_length=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    await service.get(db, connection_id)
    query = sa.select(IntegrationLink).where(IntegrationLink.connection_id == connection_id)
    if state is not None:
        query = query.where(IntegrationLink.state == state)
    if q:
        term = f"%{q.strip().lower()}%"
        query = query.where(
            sa.or_(
                sa.func.lower(IntegrationLink.external_title).like(term),
                sa.func.lower(IntegrationLink.external_sku).like(term),
            )
        )
    rows = list(
        (
            await db.scalars(
                query.order_by(IntegrationLink.external_title, IntegrationLink.id)
                .offset(offset)
                .limit(50)
            )
        ).all()
    )
    names = await catalog.internal_names(db, rows)
    counts, open_conflicts = await _counts(db, connection_id)
    return {
        "items": [catalog.link_view(row, names) for row in rows],
        "next_offset": offset + len(rows),
        "counts": counts,
        "open_conflicts": open_conflicts,
    }


@router.put("/{connection_id}/links/{link_id}")
async def map_link(
    connection_id: uuid.UUID, link_id: uuid.UUID, body: MappingInput, db: DbSession, _: Owner
) -> dict[str, Any]:
    await lock_shop(db)
    conn = await service.get(db, connection_id)
    link = await db.scalar(
        sa.select(IntegrationLink).where(
            IntegrationLink.id == link_id, IntegrationLink.connection_id == conn.id
        )
    )
    if link is None:
        raise NotFoundError()
    await catalog.set_mapping(
        db, conn, link, product_id=body.product_id, variant_id=body.variant_id, ignore=body.ignore
    )
    await service.audit(
        db,
        AuditAction.INTEGRATION_CONFIGURED,
        conn,
        change="mapping",
        link=str(link.id),
        state=link.state,
    )
    await db.flush()
    names = await catalog.internal_names(db, [link])
    return catalog.link_view(link, names)
