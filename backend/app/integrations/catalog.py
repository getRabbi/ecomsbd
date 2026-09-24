"""External catalog -> ecomsbd product mapping.

Matching is by provider id first (a link, once made, is kept), then by exact
SKU, then by a person. Names are never used: two products called "Panjabi"
are two products. An SKU that fits more than one ecomsbd item, or an ecomsbd
item another external item already holds, becomes a conflict, not a guess.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utc_now
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.integrations import sync
from app.integrations.models import IntegrationConnection, IntegrationEvent
from app.integrations.sync_models import IntegrationLink
from app.products.models import Product, ProductVariant
from app.products.service import ProductService


def external_key(item: dict[str, Any]) -> str:
    return f"{item['product_id']}:{item.get('variant_id') or ''}"


def internal_key(product_id: uuid.UUID, variant_id: uuid.UUID | None) -> str:
    return f"{product_id}:{variant_id or ''}"


def link_view(link: IntegrationLink, names: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "id": link.id,
        "external_product_id": link.external_product_id,
        "external_variant_id": link.external_variant_id,
        "external_sku": link.external_sku,
        "external_title": link.external_title,
        "external_price_paisa": link.external_price_paisa,
        "external_tracked": link.external_tracked,
        "external_qty": link.external_qty,
        "product_id": link.product_id,
        "variant_id": link.variant_id,
        "internal_name": (names or {}).get(link.internal_key or ""),
        "state": link.state,
        "match_source": link.match_source,
        "synced_qty": link.synced_qty,
        "last_synced_at": link.last_synced_at,
        "external_seen_at": link.external_seen_at,
    }


async def internal_names(db: AsyncSession, links: list[IntegrationLink]) -> dict[str, str]:
    product_ids = {link.product_id for link in links if link.product_id}
    variant_ids = {link.variant_id for link in links if link.variant_id}
    names: dict[str, str] = {}
    products = (
        {
            p.id: p
            for p in (await db.scalars(sa.select(Product).where(Product.id.in_(product_ids)))).all()
        }
        if product_ids
        else {}
    )
    variants = (
        {
            v.id: v
            for v in (
                await db.scalars(
                    sa.select(ProductVariant).where(ProductVariant.id.in_(variant_ids))
                )
            ).all()
        }
        if variant_ids
        else {}
    )
    for link in links:
        if not link.internal_key or link.product_id not in products:
            continue
        name = products[link.product_id].name
        if link.variant_id in variants:
            name = f"{name} / {variants[link.variant_id].name}"
        names[link.internal_key] = name
    return names


async def _candidates(db: AsyncSession, sku: str) -> list[tuple[uuid.UUID, uuid.UUID | None]]:
    wanted = sku.strip().lower()
    products = (
        await db.scalars(
            sa.select(Product).where(
                sa.func.lower(Product.sku) == wanted,
                Product.has_variants.is_(False),
                Product.archived_at.is_(None),
            )
        )
    ).all()
    variants = (
        await db.scalars(
            sa.select(ProductVariant).where(
                sa.func.lower(ProductVariant.sku) == wanted, ProductVariant.is_active.is_(True)
            )
        )
    ).all()
    return [(p.id, None) for p in products] + [(v.product_id, v.id) for v in variants]


async def _claim(
    db: AsyncSession,
    conn: IntegrationConnection,
    link: IntegrationLink,
    product_id: uuid.UUID,
    variant_id: uuid.UUID | None,
    source: str,
) -> bool:
    """Map ``link`` unless another external item already holds that ecomsbd item."""
    key = internal_key(product_id, variant_id)
    holder = await db.scalar(
        sa.select(IntegrationLink.id).where(
            IntegrationLink.connection_id == conn.id,
            IntegrationLink.internal_key == key,
            IntegrationLink.id != link.id,
        )
    )
    if holder is not None:
        return False
    try:
        async with db.begin_nested():
            link.product_id, link.variant_id, link.internal_key = product_id, variant_id, key
            link.state, link.match_source = "MATCHED", source
            link.synced_qty = link.local_basis = link.pending_since = None
            await db.flush()
    except IntegrityError:
        link.product_id = link.variant_id = link.internal_key = None
        link.state, link.match_source = "UNMATCHED", None
        return False
    return True


async def _create_internal(
    db: AsyncSession, conn: IntegrationConnection, link: IntegrationLink, item: dict[str, Any]
) -> tuple[uuid.UUID, uuid.UUID | None] | None:
    """Products authority EXTERNAL: bring an unknown store item in as a product.

    Stock starts at zero; the store's count arrives through inventory sync as
    an explained movement, or as a conflict the seller settles.
    """
    service = ProductService(db)
    price = item.get("price_paisa") or 0
    if not item.get("variant_name"):
        product = await service.create(
            name=item.get("product_title") or item["title"],
            sku=item["sku"],
            default_selling_price_paisa=price,
        )
        return product.id, None
    sibling = await db.scalar(
        sa.select(IntegrationLink).where(
            IntegrationLink.connection_id == conn.id,
            IntegrationLink.external_product_id == link.external_product_id,
            IntegrationLink.match_source == "CREATED",
            IntegrationLink.product_id.is_not(None),
        )
    )
    if sibling is not None and sibling.product_id is not None:
        product_id = sibling.product_id
    else:
        product = await service.create(
            name=item.get("product_title") or item["title"], default_selling_price_paisa=price
        )
        product_id = product.id
    variant = await service.add_variant(
        product_id, name=item["variant_name"], sku=item["sku"], price_paisa=price or None
    )
    return product_id, variant.id


async def match(
    db: AsyncSession, conn: IntegrationConnection, link: IntegrationLink, item: dict[str, Any]
) -> None:
    sku = link.external_sku
    if not sku:
        link.state = "UNMATCHED"
        return
    candidates = await _candidates(db, sku)
    if len(candidates) > 1:
        link.state = "CONFLICT"
        await sync.open_conflict(
            db,
            conn,
            kind="SKU_AMBIGUOUS",
            entity="PRODUCT",
            fingerprint=f"sku:{link.id}",
            link_id=link.id,
            detail={
                "external": {"sku": sku, "title": link.external_title},
                "ecomsbd": {"matches": len(candidates)},
            },
        )
        return
    if not candidates:
        if sync.settings_of(conn)["products"] == "EXTERNAL":
            try:
                async with db.begin_nested():
                    created = await _create_internal(db, conn, link, item)
            except (ConflictError, ValidationError):
                created = None
            if created and await _claim(db, conn, link, created[0], created[1], "CREATED"):
                return
        link.state = "UNMATCHED"
        return
    product_id, variant_id = candidates[0]
    if not await _claim(db, conn, link, product_id, variant_id, "SKU"):
        link.state = "CONFLICT"
        await sync.open_conflict(
            db,
            conn,
            kind="SKU_DUPLICATE",
            entity="PRODUCT",
            fingerprint=f"dup:{link.id}",
            link_id=link.id,
            detail={
                "external": {"sku": sku, "title": link.external_title},
                "ecomsbd": {"sku": sku},
            },
        )


async def apply_items(
    db: AsyncSession, conn: IntegrationConnection, items: list[dict[str, Any]]
) -> dict[str, int]:
    """Upsert one page of external items and match what is new."""
    counts = {"seen": 0, "matched": 0, "unmatched": 0, "conflicts": 0}
    now = utc_now()
    authority = sync.settings_of(conn)["products"]
    for item in items:
        if not item.get("product_id"):
            continue
        counts["seen"] += 1
        key = external_key(item)
        link = await db.scalar(
            sa.select(IntegrationLink).where(
                IntegrationLink.connection_id == conn.id, IntegrationLink.external_key == key
            )
        )
        if link is None:
            link = IntegrationLink(
                tenant_id=conn.tenant_id,
                connection_id=conn.id,
                external_key=key,
                external_product_id=item["product_id"],
                external_variant_id=item.get("variant_id"),
                external_title=item["title"],
                state="UNMATCHED",
            )
            db.add(link)
            await db.flush()
        price_changed = (
            link.external_price_paisa is not None
            and item.get("price_paisa") is not None
            and item["price_paisa"] != link.external_price_paisa
        )
        link.external_inventory_id = item.get("inventory_id") or link.external_inventory_id
        link.external_sku = item.get("sku")
        link.external_title = item["title"]
        link.external_price_paisa = item.get("price_paisa")
        link.external_tracked = bool(item.get("tracked"))
        if item.get("qty") is not None:
            link.external_qty = item["qty"]
        link.external_seen_at = now
        if link.state == "DELETED":
            link.state = "MATCHED" if link.internal_key else "UNMATCHED"
        if link.state in {"UNMATCHED", "CONFLICT"} and link.match_source != "MANUAL":
            await match(db, conn, link, item)
        elif link.state == "MATCHED" and price_changed and authority == "EXTERNAL":
            await _adopt_price(db, link)
        counts[{"MATCHED": "matched", "CONFLICT": "conflicts"}.get(link.state, "unmatched")] += 1
    await db.flush()  # sessions do not autoflush; the scan's end reads these rows
    return counts


async def _adopt_price(db: AsyncSession, link: IntegrationLink) -> None:
    if link.product_id is None or link.external_price_paisa is None:
        return
    service = ProductService(db)
    if link.variant_id is not None:
        await service.update_variant(
            link.product_id, link.variant_id, price_paisa=link.external_price_paisa
        )
    else:
        await service.update(link.product_id, default_selling_price_paisa=link.external_price_paisa)


async def finish_scan(db: AsyncSession, conn: IntegrationConnection, started: datetime) -> int:
    """After a complete scan, items the store no longer lists were deleted there."""
    await db.flush()
    gone = (
        await db.scalars(
            sa.select(IntegrationLink).where(
                IntegrationLink.connection_id == conn.id,
                IntegrationLink.state.in_(["MATCHED", "UNMATCHED", "CONFLICT"]),
                sa.or_(
                    IntegrationLink.external_seen_at.is_(None),
                    IntegrationLink.external_seen_at < started,
                ),
            )
        )
    ).all()
    for link in gone:
        was_mapped = link.internal_key is not None
        link.state = "DELETED"
        if was_mapped:
            await sync.open_conflict(
                db,
                conn,
                kind="EXTERNAL_ITEM_DELETED",
                entity="PRODUCT",
                fingerprint=f"deleted:{link.id}",
                link_id=link.id,
                detail={"external": {"title": link.external_title, "sku": link.external_sku}},
            )
    conn.config = {**conn.config, "catalog_synced_at": utc_now().isoformat()}
    return len(gone)


async def set_mapping(
    db: AsyncSession,
    conn: IntegrationConnection,
    link: IntegrationLink,
    *,
    product_id: uuid.UUID | None,
    variant_id: uuid.UUID | None,
    ignore: bool = False,
) -> IntegrationLink:
    """A person maps, unmaps or ignores one external item."""
    if ignore:
        link.product_id = link.variant_id = link.internal_key = None
        link.state, link.match_source = "IGNORED", "MANUAL"
        await sync.close_for_link(db, link.id, "IGNORE_ITEM")
        return link
    if product_id is None:
        link.product_id = link.variant_id = link.internal_key = None
        link.state, link.match_source = "UNMATCHED", "MANUAL"
        link.synced_qty = link.local_basis = link.pending_since = None
        return link
    product = await db.scalar(sa.select(Product).where(Product.id == product_id))
    if product is None:
        raise NotFoundError()
    if product.has_variants and variant_id is None:
        raise ValidationError(
            "Choose which variant this item is", details={"code": "VARIANT_REQUIRED"}
        )
    if variant_id is not None:
        variant = await db.scalar(
            sa.select(ProductVariant).where(
                ProductVariant.id == variant_id, ProductVariant.product_id == product.id
            )
        )
        if variant is None:
            raise NotFoundError()
    if not await _claim(db, conn, link, product.id, variant_id, "MANUAL"):
        raise ConflictError(
            "Another store item is already mapped to that product",
            details={"code": "ALREADY_MAPPED"},
        )
    await sync.close_for_link(db, link.id, "MAP_MANUALLY")
    return link


async def schedule_price_push(
    db: AsyncSession,
    conn: IntegrationConnection,
    product_id: uuid.UUID,
    variant_id: uuid.UUID | None,
) -> int:
    """Products authority ECOMSBD: a price edit in ecomsbd goes to the mapped store item."""
    if not sync.can(conn, "price_push"):
        return 0
    links = (
        await db.scalars(
            sa.select(IntegrationLink).where(
                IntegrationLink.connection_id == conn.id,
                IntegrationLink.state == "MATCHED",
                IntegrationLink.product_id == product_id,
                IntegrationLink.variant_id == variant_id
                if variant_id
                else IntegrationLink.variant_id.is_(None),
            )
        )
    ).all()
    queued = 0
    for link in links:
        product = await db.get(Product, product_id)
        variant = await db.get(ProductVariant, variant_id) if variant_id else None
        price = (variant.price_paisa if variant and variant.price_paisa is not None else None) or (
            product.default_selling_price_paisa if product else None
        )
        if price is None or price == link.external_price_paisa:
            continue
        queued += await queue(
            db,
            conn,
            operation="PUSH_PRICE",
            key=f"price:{link.id}:{price}",
            payload={"link_id": str(link.id), "price_paisa": price},
        )
    return queued


async def queue(
    db: AsyncSession,
    conn: IntegrationConnection,
    *,
    operation: str,
    key: str,
    payload: dict[str, Any],
    order_id: uuid.UUID | None = None,
) -> int:
    """One durable outbound operation per key. A replay adds nothing."""
    row = IntegrationEvent(
        tenant_id=conn.tenant_id,
        connection_id=conn.id,
        provider=conn.provider,
        kind="OUTBOUND",
        topic=operation.lower(),
        operation=operation,
        delivery_id=key[:200],
        payload=payload,
        order_id=order_id,
        status="QUEUED",
        next_attempt_at=utc_now(),
    )
    try:
        async with db.begin_nested():
            db.add(row)
            await db.flush()
    except IntegrityError:
        return 0
    return 1
