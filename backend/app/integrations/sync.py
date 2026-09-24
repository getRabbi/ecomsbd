"""The shared two-way sync layer: settings, capabilities and conflicts.

Providers differ in what they can do, never in what is true. Every provider
adapter reads and writes the same internal things — ledger stock, order
status, consignment tracking — through the same operations here, and every
ambiguous case becomes an :class:`IntegrationConflict` for a person.
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utc_now
from app.core.errors import ConflictError, ValidationError
from app.integrations import shopify
from app.integrations.models import IntegrationConnection
from app.integrations.sync_models import IntegrationConflict

#: Conservative: nothing is written to the store or to the ledger until the
#: seller turns it on. Orders always flow in; ecomsbd always runs operations.
DEFAULT_SYNC: dict[str, Any] = {
    "catalog": False,
    "products": "MANUAL",
    "inventory": "NONE",
    "order_status": "OFF",
    "fulfillment": "OFF",
    "location_id": None,
}
CHOICES = {
    "products": ("MANUAL", "EXTERNAL", "ECOMSBD"),
    "inventory": ("NONE", "ECOMSBD", "EXTERNAL"),
    "order_status": ("OFF", "TWO_WAY"),
    "fulfillment": ("OFF", "ON"),
}
SYNC_PROVIDERS = frozenset({"SHOPIFY", "WOOCOMMERCE"})

#: What each provider can do with each ecomsbd order state. None: not pushed,
#: because the store has no honest equivalent.
STATUS_MAP: dict[str, dict[str, str | None]] = {
    "SHOPIFY": {
        "CONFIRMED": None,
        "CANCELLED": "orderCancel",
        "FULFILLED": "fulfillmentCreate + tracking",
        "DELIVERED": "fulfillment event DELIVERED",
        "RETURNED": None,
    },
    "WOOCOMMERCE": {
        "CONFIRMED": "processing",
        "CANCELLED": "cancelled",
        "FULFILLED": "order note with courier and tracking",
        "DELIVERED": "completed",
        "RETURNED": None,
    },
}

#: kind -> (recommended action, allowed resolutions).
CONFLICT_RULES: dict[str, tuple[str, tuple[str, ...]]] = {
    "SKU_AMBIGUOUS": ("MAP_MANUALLY", ("IGNORE_ITEM", "DISMISS")),
    "SKU_DUPLICATE": ("MAP_MANUALLY", ("IGNORE_ITEM", "DISMISS")),
    "EXTERNAL_ITEM_DELETED": ("UNMAP", ("UNMAP", "DISMISS")),
    "STOCK_INITIAL_MISMATCH": ("USE_ECOMSBD", ("USE_ECOMSBD", "USE_EXTERNAL", "ACCEPT_DIFFERENCE")),
    "STOCK_CHANGED_EXTERNALLY": (
        "USE_ECOMSBD",
        ("USE_ECOMSBD", "USE_EXTERNAL", "ACCEPT_DIFFERENCE"),
    ),
    "STOCK_CHANGED_IN_ECOMSBD": (
        "USE_EXTERNAL",
        ("USE_ECOMSBD", "USE_EXTERNAL", "ACCEPT_DIFFERENCE"),
    ),
    "STOCK_CHANGED_BOTH": ("USE_ECOMSBD", ("USE_ECOMSBD", "USE_EXTERNAL", "ACCEPT_DIFFERENCE")),
    "CANCELLED_AFTER_BOOKING": ("CONTACT_COURIER", ("KEEP_ORDER", "HANDLED_WITH_COURIER")),
    "ORDER_CHANGED_EXTERNALLY": ("REVIEW_ORDER", ("MARK_REVIEWED",)),
    "STATUS_DIVERGED": ("REVIEW_ORDER", ("MARK_REVIEWED",)),
}
STOCK_CONFLICTS = frozenset(k for k in CONFLICT_RULES if k.startswith("STOCK_"))


def settings_of(conn: IntegrationConnection) -> dict[str, Any]:
    return {**DEFAULT_SYNC, **(conn.config.get("sync") or {})}


def features(sync: dict[str, Any]) -> list[str]:
    """The Shopify scope groups these settings need."""
    wanted = []
    if sync["catalog"] or sync["inventory"] != "NONE" or sync["products"] != "MANUAL":
        wanted.append("catalog")
    if sync["products"] == "ECOMSBD":
        wanted.append("price_push")
    if sync["inventory"] != "NONE":
        wanted.append("inventory")
    if sync["order_status"] == "TWO_WAY":
        wanted.append("order_status")
    if sync["fulfillment"] == "ON":
        wanted.append("fulfillment")
    return wanted


def validate(conn: IntegrationConnection, body: dict[str, Any]) -> dict[str, Any]:
    if conn.provider not in SYNC_PROVIDERS:
        raise ValidationError("This connection syncs through its API and webhooks")
    sync = settings_of(conn)
    for key, value in body.items():
        if value is None:
            continue
        if key in CHOICES and value not in CHOICES[key]:
            raise ValidationError("Choose one of the offered options", details={"field": key})
        sync[key] = value
    if sync["inventory"] != "NONE" or sync["products"] != "MANUAL":
        sync["catalog"] = True  # stock and product sync only work through mappings
    return sync


def capabilities(conn: IntegrationConnection) -> dict[str, dict[str, Any]]:
    """Per feature: can this connection do it, is it on, and what blocks it."""
    sync = settings_of(conn)
    enabled = set(features(sync))
    result: dict[str, dict[str, Any]] = {}
    for feature in ("catalog", "price_push", "inventory", "order_status", "fulfillment"):
        blocker = None
        if conn.provider not in SYNC_PROVIDERS:
            blocker = "USE_API_AND_WEBHOOKS"
        elif (
            conn.provider == "SHOPIFY"
            and feature in enabled
            and shopify.missing_scopes(conn.config.get("scopes"), feature)
        ):
            blocker = "RECONNECT_FOR_PERMISSION"
        if feature == "inventory" and conn.provider == "SHOPIFY" and not sync["location_id"]:
            blocker = blocker or "CHOOSE_LOCATION"
        result[feature] = {"enabled": feature in enabled, "blocker": blocker}
    return result


def can(conn: IntegrationConnection, feature: str) -> bool:
    cap = capabilities(conn).get(feature) or {}
    return bool(cap.get("enabled")) and cap.get("blocker") is None and conn.state == "CONNECTED"


def conflict_view(row: IntegrationConflict) -> dict[str, Any]:
    return {
        key: getattr(row, key)
        for key in (
            "id",
            "connection_id",
            "provider",
            "kind",
            "entity",
            "link_id",
            "order_id",
            "detail",
            "recommended",
            "options",
            "status",
            "resolution",
            "resolved_at",
            "created_at",
            "updated_at",
        )
    }


async def open_conflict(
    db: AsyncSession,
    conn: IntegrationConnection,
    *,
    kind: str,
    entity: str,
    fingerprint: str,
    detail: dict[str, Any],
    link_id: uuid.UUID | None = None,
    order_id: uuid.UUID | None = None,
    recommended: str | None = None,
) -> IntegrationConflict:
    """One open conflict per fingerprint; a repeat refreshes what each side says."""
    existing = await db.scalar(
        sa.select(IntegrationConflict).where(
            IntegrationConflict.connection_id == conn.id,
            IntegrationConflict.open_key == fingerprint,
        )
    )
    rule, options = CONFLICT_RULES[kind]
    if existing is not None:
        existing.detail, existing.kind = detail, kind
        existing.recommended, existing.options = recommended or rule, list(options)
        return existing
    row = IntegrationConflict(
        tenant_id=conn.tenant_id,
        connection_id=conn.id,
        provider=conn.provider,
        kind=kind,
        entity=entity,
        link_id=link_id,
        order_id=order_id,
        detail=detail,
        recommended=recommended or rule,
        options=list(options),
        status="OPEN",
        open_key=fingerprint,
    )
    try:
        async with db.begin_nested():
            db.add(row)
            await db.flush()
    except IntegrityError:
        raced = await db.scalar(
            sa.select(IntegrationConflict).where(
                IntegrationConflict.connection_id == conn.id,
                IntegrationConflict.open_key == fingerprint,
            )
        )
        if raced is None:
            raise
        return raced
    return row


async def has_open(db: AsyncSession, conn_id: uuid.UUID, fingerprint: str) -> bool:
    return (
        await db.scalar(
            sa.select(IntegrationConflict.id).where(
                IntegrationConflict.connection_id == conn_id,
                IntegrationConflict.open_key == fingerprint,
            )
        )
    ) is not None


def close(row: IntegrationConflict, resolution: str, actor_id: uuid.UUID | None) -> None:
    if row.status != "OPEN":
        raise ConflictError("This conflict is already closed", details={"code": "CONFLICT_CLOSED"})
    row.status = "DISMISSED" if resolution == "DISMISS" else "RESOLVED"
    row.resolution, row.resolved_by, row.resolved_at = resolution, actor_id, utc_now()
    row.open_key = None


async def close_for_link(db: AsyncSession, link_id: uuid.UUID, resolution: str) -> None:
    """Mapping a link by hand settles the mapping conflicts about it."""
    rows = (
        await db.scalars(
            sa.select(IntegrationConflict).where(
                IntegrationConflict.link_id == link_id,
                IntegrationConflict.status == "OPEN",
                IntegrationConflict.kind.in_(["SKU_AMBIGUOUS", "SKU_DUPLICATE"]),
            )
        )
    ).all()
    for row in rows:
        if row.status == "OPEN":
            close(row, resolution, None)
