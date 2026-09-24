"""Shopify two-way sync operations, from shopify.dev only (GraphQL Admin 2026-07).

- Catalog: ``productVariants`` (every product has at least one variant, and
  stock lives on each variant's InventoryItem).
- Stock: ``inventorySetQuantities`` with ``changeFromQuantity`` (mandatory from
  2026-04) so a change made in the store meanwhile fails instead of being
  overwritten, and ``@idempotent`` (mandatory from 2026-04) so a retry applies
  once.
- Fulfillment: ``fulfillmentCreate`` on the order's open fulfillment orders
  with the courier as tracking company; delivery via ``fulfillmentEventCreate``.
- Cancellation: ``orderCancel`` (asynchronous job). No refund is requested:
  money is the seller's decision, not a side effect of a status sync.

Returns are not pushed: Shopify's returns API needs per-line return requests
ecomsbd does not model, and an invented mapping would misstate the order.
"""

from __future__ import annotations

from typing import Any

from app.integrations.http import ProviderError
from app.integrations.normalize import paisa
from app.integrations.shopify import graphql

LOCATIONS_QUERY = (
    "query { locations(first: 50) { nodes { id name isActive fulfillsOnlineOrders } } }"
)
VARIANTS_QUERY = """
query Variants($first: Int!, $after: String) {
  productVariants(first: $first, after: $after) {
    nodes {
      id legacyResourceId sku title displayName price updatedAt
      product { id legacyResourceId title status }
      inventoryItem { id tracked }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""
LEVELS_QUERY = """
query Levels($ids: [ID!]!, $location: ID!) {
  nodes(ids: $ids) {
    ... on InventoryItem {
      id
      inventoryLevel(locationId: $location) { quantities(names: ["available"]) { name quantity } }
    }
  }
}
"""
ORDER_FULFILLMENT_QUERY = """
query OrderFulfillment($id: ID!) {
  order(id: $id) {
    id cancelledAt displayFulfillmentStatus
    fulfillmentOrders(first: 20) { nodes { id status } }
    fulfillments(first: 20) { id status trackingInfo { number company } }
  }
}
"""
FULFILL_MUTATION = """
mutation Fulfill($fulfillment: FulfillmentInput!) {
  fulfillmentCreate(fulfillment: $fulfillment) {
    fulfillment { id status }
    userErrors { field message }
  }
}
"""
EVENT_MUTATION = """
mutation Event($event: FulfillmentEventInput!) {
  fulfillmentEventCreate(fulfillmentEvent: $event) {
    fulfillmentEvent { id status }
    userErrors { field message }
  }
}
"""
CANCEL_MUTATION = """
mutation Cancel($orderId: ID!, $staffNote: String) {
  orderCancel(
    orderId: $orderId, reason: OTHER, restock: true, notifyCustomer: false, staffNote: $staffNote
  ) {
    job { id }
    orderCancelUserErrors { code field message }
  }
}
"""
PRICE_MUTATION = """
mutation Price($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
  productVariantsBulkUpdate(productId: $productId, variants: $variants) {
    productVariants { id price }
    userErrors { field message }
  }
}
"""
CATALOG_PAGE = 100


def gid(kind: str, value: str) -> str:
    return value if value.startswith("gid://") else f"gid://shopify/{kind}/{value}"


def numeric(value: str | None) -> str | None:
    return value.rsplit("/", 1)[-1] if value else None


async def locations(shop: str, credentials: dict[str, Any]) -> list[dict[str, Any]]:
    data = (await graphql(shop, credentials, LOCATIONS_QUERY)).get("locations") or {}
    return [
        {
            "id": numeric(node.get("id")),
            "name": node.get("name") or "",
            "active": bool(node.get("isActive")),
            "online": bool(node.get("fulfillsOnlineOrders")),
        }
        for node in data.get("nodes") or []
        if node.get("id")
    ]


def catalog_item(node: dict[str, Any]) -> dict[str, Any]:
    """A Shopify variant as one sellable external item."""
    product = node.get("product") or {}
    item = node.get("inventoryItem") or {}
    title = product.get("title") or ""
    if node.get("title") and node.get("title") != "Default Title":
        title = f"{title} / {node['title']}"
    return {
        "product_id": str(product.get("legacyResourceId") or numeric(product.get("id")) or ""),
        "variant_id": str(node.get("legacyResourceId") or numeric(node.get("id")) or ""),
        "inventory_id": numeric(item.get("id")),
        "sku": (node.get("sku") or "").strip() or None,
        "title": title[:300] or "Shopify item",
        "price_paisa": paisa(node.get("price")) if node.get("price") is not None else None,
        "tracked": bool(item.get("tracked")),
        "variant_name": None
        if node.get("title") in (None, "Default Title")
        else str(node["title"])[:120],
        "product_title": (product.get("title") or "Shopify item")[:200],
    }


async def catalog_page(
    shop: str, credentials: dict[str, Any], cursor: str | None
) -> tuple[list[dict[str, Any]], str | None]:
    data = (
        await graphql(shop, credentials, VARIANTS_QUERY, {"first": CATALOG_PAGE, "after": cursor})
    ).get("productVariants") or {}
    info = data.get("pageInfo") or {}
    items = [catalog_item(node) for node in data.get("nodes") or []]
    return items, info.get("endCursor") if info.get("hasNextPage") else None


async def stock_levels(
    shop: str, credentials: dict[str, Any], inventory_ids: list[str], location_id: str
) -> dict[str, int | None]:
    """Available quantity per inventory item at one location; None if not stocked there."""
    levels: dict[str, int | None] = {}
    for start in range(0, len(inventory_ids), 100):
        chunk = inventory_ids[start : start + 100]
        data = await graphql(
            shop,
            credentials,
            LEVELS_QUERY,
            {
                "ids": [gid("InventoryItem", i) for i in chunk],
                "location": gid("Location", location_id),
            },
        )
        for node in data.get("nodes") or []:
            if not node or not node.get("id"):
                continue
            level = node.get("inventoryLevel") or {}
            quantities = {q.get("name"): q.get("quantity") for q in level.get("quantities") or []}
            levels[numeric(node["id"]) or ""] = (
                int(quantities["available"]) if "available" in quantities else None
            )
    return levels


async def set_stock(
    shop: str,
    credentials: dict[str, Any],
    *,
    inventory_id: str,
    location_id: str,
    quantity: int,
    change_from: int | None,
    idempotency_key: str,
) -> None:
    if not idempotency_key.replace("-", "").isalnum():
        raise ProviderError("MALFORMED_PAYLOAD")
    # The directive takes a literal; the key is ecomsbd's own event id.
    mutation = (
        "mutation Set($input: InventorySetQuantitiesInput!) {"
        f' inventorySetQuantities(input: $input) @idempotent(key: "{idempotency_key}") {{'
        " inventoryAdjustmentGroup { id } userErrors { code field message } } }"
    )
    result = (
        await graphql(
            shop,
            credentials,
            mutation,
            {
                "input": {
                    "name": "available",
                    "reason": "correction",
                    "referenceDocumentUri": f"ecomsbd://integration-sync/{idempotency_key}",
                    "quantities": [
                        {
                            "inventoryItemId": gid("InventoryItem", inventory_id),
                            "locationId": gid("Location", location_id),
                            "quantity": quantity,
                            "changeFromQuantity": change_from,
                        }
                    ],
                }
            },
        )
    ).get("inventorySetQuantities") or {}
    codes = {e.get("code") for e in result.get("userErrors") or []}
    if codes & {"CHANGE_FROM_QUANTITY_STALE", "COMPARE_QUANTITY_STALE"}:
        raise ProviderError("STOCK_CHANGED_DURING_PUSH")
    if codes & {"ITEM_NOT_STOCKED_AT_LOCATION", "INVALID_LOCATION", "INVALID_INVENTORY_ITEM"}:
        raise ProviderError("NOT_STOCKED_AT_LOCATION")
    if codes:
        raise ProviderError("PROVIDER_REJECTED")


async def set_price(
    shop: str, credentials: dict[str, Any], *, product_id: str, variant_id: str, price_paisa: int
) -> None:
    result = (
        await graphql(
            shop,
            credentials,
            PRICE_MUTATION,
            {
                "productId": gid("Product", product_id),
                "variants": [
                    {"id": gid("ProductVariant", variant_id), "price": f"{price_paisa / 100:.2f}"}
                ],
            },
        )
    ).get("productVariantsBulkUpdate") or {}
    if result.get("userErrors"):
        raise ProviderError("PROVIDER_REJECTED")


async def order_fulfillment(
    shop: str, credentials: dict[str, Any], order_id: str
) -> dict[str, Any]:
    order = (
        await graphql(shop, credentials, ORDER_FULFILLMENT_QUERY, {"id": gid("Order", order_id)})
    ).get("order")
    if not order:
        raise ProviderError("NOT_FOUND_AT_PROVIDER")
    return order


async def fulfill(
    shop: str, credentials: dict[str, Any], order_id: str, *, carrier: str, tracking: str | None
) -> str:
    """Fulfil the order's open fulfillment orders with the courier's tracking.

    Idempotent against the store: an order with nothing left open returns the
    fulfillment already there instead of creating another.
    """
    order = await order_fulfillment(shop, credentials, order_id)
    if order.get("cancelledAt"):
        raise ProviderError("ORDER_CANCELLED_AT_PROVIDER")
    open_ids = [
        fo["id"]
        for fo in (order.get("fulfillmentOrders") or {}).get("nodes") or []
        if fo.get("status") in {"OPEN", "IN_PROGRESS"}
    ]
    if not open_ids:
        existing = [f for f in order.get("fulfillments") or [] if f.get("id")]
        for item in existing:
            numbers = {t.get("number") for t in item.get("trackingInfo") or []}
            if tracking is None or tracking in numbers:
                return str(item["id"])
        if existing:
            return str(existing[-1]["id"])
        raise ProviderError("NOTHING_TO_FULFILL")
    tracking_info: dict[str, Any] = {"company": carrier[:60]}
    if tracking:
        tracking_info["number"] = tracking[:120]
    result = (
        await graphql(
            shop,
            credentials,
            FULFILL_MUTATION,
            {
                "fulfillment": {
                    "lineItemsByFulfillmentOrder": [{"fulfillmentOrderId": i} for i in open_ids],
                    "notifyCustomer": False,
                    "trackingInfo": tracking_info,
                }
            },
        )
    ).get("fulfillmentCreate") or {}
    created = result.get("fulfillment") or {}
    if result.get("userErrors") or not created.get("id"):
        raise ProviderError("PROVIDER_REJECTED")
    return str(created["id"])


async def mark_delivered(
    shop: str, credentials: dict[str, Any], fulfillment_id: str, happened_at: str
) -> None:
    result = (
        await graphql(
            shop,
            credentials,
            EVENT_MUTATION,
            {
                "event": {
                    "fulfillmentId": gid("Fulfillment", fulfillment_id),
                    "status": "DELIVERED",
                    "happenedAt": happened_at,
                }
            },
        )
    ).get("fulfillmentEventCreate") or {}
    if result.get("userErrors"):
        raise ProviderError("PROVIDER_REJECTED")


async def cancel(shop: str, credentials: dict[str, Any], order_id: str) -> None:
    order = await order_fulfillment(shop, credentials, order_id)
    if order.get("cancelledAt"):
        return  # already cancelled in the store: done, not an error
    result = (
        await graphql(
            shop,
            credentials,
            CANCEL_MUTATION,
            {"orderId": gid("Order", order_id), "staffNote": "Cancelled in ecomsbd"},
        )
    ).get("orderCancel") or {}
    if result.get("orderCancelUserErrors"):
        raise ProviderError("PROVIDER_REJECTED")


def provider_status(node: dict[str, Any]) -> str:
    """The store's own word for an order, kept on the order's sync record."""
    if node.get("cancelledAt"):
        return "CANCELLED"
    return str(
        node.get("displayFulfillmentStatus") or node.get("displayFinancialStatus") or "OPEN"
    )[:40]
