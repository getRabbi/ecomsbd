"""WooCommerce two-way sync operations, WooCommerce core REST API v3 only.

Core covers products, variations, stock (``stock_quantity`` on items with
``manage_stock``), prices, order status and order notes. It has no shipment
tracking fields: tracking is written as an order note, which is core. The
official Shipment Tracking extension is paid, optional and needs a carrier
tracking-link template that the Bangladeshi couriers' documentation in this
project does not provide, so ecomsbd does not invent one.

Status mapping (core statuses only; nothing is invented):
    CONFIRMED  -> processing  (only from pending or on-hold)
    CANCELLED  -> cancelled
    DELIVERED  -> completed
    RETURNED   -> not pushed: core's nearest word is "refunded", which claims
                  money moved.
"""

from __future__ import annotations

from typing import Any

from app.integrations.http import ProviderError
from app.integrations.normalize import clip, paisa
from app.integrations.woocommerce import call

CATALOG_PAGE = 100
STATUS_FOR = {"CONFIRMED": "processing", "CANCELLED": "cancelled", "DELIVERED": "completed"}
#: A confirmation is only written over these; anything later is the store's.
CONFIRMABLE_FROM = frozenset({"pending", "on-hold"})
FINAL = frozenset({"completed", "cancelled", "refunded", "failed", "trash"})
NOTE_MARKER = "[ecomsbd:{key}]"


def catalog_item(
    product: dict[str, Any], variation: dict[str, Any] | None = None
) -> dict[str, Any]:
    item = variation or product
    title = product.get("name") or "WooCommerce item"
    variant_name = None
    if variation is not None:
        options = [
            str(a.get("option")) for a in variation.get("attributes") or [] if a.get("option")
        ]
        variant_name = " / ".join(options)[:120] or f"#{variation.get('id')}"
        title = f"{title} / {variant_name}"
    price = item.get("regular_price") or item.get("price")
    return {
        "product_id": str(product.get("id")),
        "variant_id": str(variation["id"]) if variation is not None else None,
        "inventory_id": None,
        "sku": (item.get("sku") or "").strip() or None,
        "title": title[:300],
        "price_paisa": paisa(price) if price not in (None, "") else None,
        "tracked": bool(item.get("manage_stock")) and item.get("manage_stock") != "parent",
        "qty": item.get("stock_quantity") if isinstance(item.get("stock_quantity"), int) else None,
        "variant_name": variant_name,
        "product_title": clip(product.get("name"), 200) or "WooCommerce item",
    }


async def _all_pages(
    store: str, credentials: dict[str, Any], path: str, params: dict[str, Any]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page = 1
    while True:
        reply = await call(
            store,
            credentials,
            "GET",
            path,
            params={**params, "per_page": CATALOG_PAGE, "page": page},
        )
        if not isinstance(reply.data, list):
            raise ProviderError("MALFORMED_RESPONSE")
        rows.extend(r for r in reply.data if isinstance(r, dict))
        pages = int(reply.headers.get("x-wp-totalpages") or 1)
        if page >= pages or not reply.data:
            return rows
        page += 1


async def catalog_page(
    store: str, credentials: dict[str, Any], cursor: str | None
) -> tuple[list[dict[str, Any]], str | None]:
    """One page of products, expanded to sellable items (simple or each variation)."""
    page = int(cursor or "1")
    reply = await call(
        store,
        credentials,
        "GET",
        "/products",
        params={"per_page": CATALOG_PAGE, "page": page, "orderby": "id", "order": "asc"},
    )
    if not isinstance(reply.data, list):
        raise ProviderError("MALFORMED_RESPONSE")
    items: list[dict[str, Any]] = []
    for product in reply.data:
        if not isinstance(product, dict) or product.get("status") == "trash":
            continue
        if product.get("type") == "variable":
            for variation in await _all_pages(
                store, credentials, f"/products/{int(product['id'])}/variations", {}
            ):
                items.append(catalog_item(product, variation))
        elif product.get("type") in {"simple", None}:
            items.append(catalog_item(product))
    pages = int(reply.headers.get("x-wp-totalpages") or 1)
    return items, str(page + 1) if page < pages and reply.data else None


async def stock_levels(
    store: str, credentials: dict[str, Any], items: list[tuple[str, str | None]]
) -> dict[tuple[str, str | None], int | None]:
    """Current stock_quantity per (product id, variation id); None when not managed."""
    levels: dict[tuple[str, str | None], int | None] = {}
    simple = [p for p, v in items if v is None]
    for start in range(0, len(simple), CATALOG_PAGE):
        chunk = simple[start : start + CATALOG_PAGE]
        for row in await _all_pages(store, credentials, "/products", {"include": ",".join(chunk)}):
            qty = row.get("stock_quantity")
            levels[(str(row.get("id")), None)] = (
                qty if row.get("manage_stock") is True and isinstance(qty, int) else None
            )
    parents: dict[str, list[str]] = {}
    for product_id, variant_id in items:
        if variant_id is not None:
            parents.setdefault(product_id, []).append(variant_id)
    for product_id, variant_ids in parents.items():
        for row in await _all_pages(
            store,
            credentials,
            f"/products/{int(product_id)}/variations",
            {"include": ",".join(variant_ids)},
        ):
            qty = row.get("stock_quantity")
            managed = row.get("manage_stock") is True
            levels[(product_id, str(row.get("id")))] = (
                qty if managed and isinstance(qty, int) else None
            )
    return levels


def _item_path(product_id: str, variant_id: str | None) -> str:
    base = f"/products/{int(product_id)}"
    return f"{base}/variations/{int(variant_id)}" if variant_id else base


async def set_stock(
    store: str, credentials: dict[str, Any], product_id: str, variant_id: str | None, quantity: int
) -> None:
    """An absolute set, so a retried request lands on the same number."""
    await call(
        store,
        credentials,
        "PUT",
        _item_path(product_id, variant_id),
        body={"stock_quantity": quantity},
    )


async def set_price(
    store: str,
    credentials: dict[str, Any],
    product_id: str,
    variant_id: str | None,
    price_paisa: int,
) -> None:
    await call(
        store,
        credentials,
        "PUT",
        _item_path(product_id, variant_id),
        body={"regular_price": f"{price_paisa / 100:.2f}"},
    )


async def order_status(store: str, credentials: dict[str, Any], order_id: str) -> str:
    reply = await call(store, credentials, "GET", f"/orders/{int(order_id)}")
    if not isinstance(reply.data, dict):
        raise ProviderError("MALFORMED_RESPONSE")
    return str(reply.data.get("status") or "")


async def push_status(store: str, credentials: dict[str, Any], order_id: str, target: str) -> str:
    """Write one ecomsbd state as the core status, never over a later store state."""
    status = STATUS_FOR.get(target)
    if status is None:
        raise ProviderError("NOT_SUPPORTED_BY_PROVIDER")
    current = await order_status(store, credentials, order_id)
    if current == status:
        return current
    if target == "CONFIRMED" and current not in CONFIRMABLE_FROM:
        return current  # the store has moved on; confirming would go backwards
    if current in FINAL:
        raise ProviderError("STORE_STATUS_FINAL")
    await call(store, credentials, "PUT", f"/orders/{int(order_id)}", body={"status": status})
    return status


async def add_tracking_note(
    store: str,
    credentials: dict[str, Any],
    order_id: str,
    *,
    key: str,
    carrier: str,
    tracking: str | None,
) -> str:
    """A private order note with the courier and tracking code, written once.

    Notes have no idempotency key, so the note carries ecomsbd's operation key
    and an existing note with it means the push already happened.
    """
    marker = NOTE_MARKER.format(key=key)
    for note in await _all_pages(store, credentials, f"/orders/{int(order_id)}/notes", {}):
        if marker in str(note.get("note") or ""):
            return str(note.get("id"))
    text = f"Booked with {carrier}" + (f" · tracking {tracking}" if tracking else "") + f" {marker}"
    reply = await call(
        store,
        credentials,
        "POST",
        f"/orders/{int(order_id)}/notes",
        body={"note": text, "customer_note": False},
    )
    if not isinstance(reply.data, dict) or reply.data.get("id") is None:
        raise ProviderError("MALFORMED_RESPONSE")
    return str(reply.data["id"])
