"""Carrybee's documented contract, as constants (V4 WP7.2).

Every value here comes from ``docs/providers/carrybee/CONTRACT.md`` — the
normalized reading of Carrybee's own developer reference
(https://merchant.carrybee.com/webhook/documentation, read 2026-11-23). Nothing
here is inferred; what the page does not state is not encoded.
"""

from __future__ import annotations

from typing import Final

PROVIDER: Final = "carrybee"

#: CONTRACT.md §1 — two isolated environments, each with its own credentials.
LIVE_BASE_URL: Final = "https://developers.carrybee.com"
SANDBOX_BASE_URL: Final = "https://sandbox.carrybee.com"

#: CONTRACT.md §1 — sent on every request.
HEADER_CLIENT_ID: Final = "Client-ID"
HEADER_CLIENT_SECRET: Final = "Client-Secret"  # noqa: S105 - a header name
HEADER_CLIENT_CONTEXT: Final = "Client-Context"

#: CONTRACT.md §5, §6.
PATH_STORES: Final = "/api/v2/stores"
PATH_ORDERS: Final = "/api/v2/orders"
PATH_ORDER_DETAILS: Final = "/api/v2/orders/{reference}/details"
PATH_ORDER_CANCEL: Final = "/api/v2/orders/{reference}/cancel"

#: CONTRACT.md §3.
DELIVERY_TYPE_NORMAL: Final = 1
PRODUCT_TYPE_PARCEL: Final = 1

#: CONTRACT.md §6.1 / §6.4: (minimum, maximum) characters, inclusive.
#: ``< 50`` / ``< 255`` / ``< 200`` on the page become 49 / 254 / 199 here.
TEXT_LIMITS: Final[dict[str, tuple[int, int]]] = {
    "merchant_order_id": (1, 49),
    "recipient_name": (2, 99),
    "recipient_address": (10, 200),
    "special_instruction": (0, 254),
    "product_description": (0, 254),
    "cancellation_reason": (1, 199),
}
WEIGHT_GRAMS_RANGE: Final = (1, 25_000)
QUANTITY_RANGE: Final = (1, 200)
#: Whole Taka (CONTRACT.md §3).
COLLECTABLE_TAKA_RANGE: Final = (0, 100_000)

#: CONTRACT.md §8: the shared secret Carrybee sends, and the reply must echo.
WEBHOOK_SECRET_HEADER: Final = "X-CB-Webhook-Integration-Header"  # noqa: S105 - a header name

#: CONTRACT.md §8: every event the page lists, in its order.
WEBHOOK_EVENTS: Final[tuple[str, ...]] = (
    "order.created",
    "order.create-failed",
    "order.updated",
    "order.pickup-requested",
    "order.assigned-for-pickup",
    "order.picked",
    "order.pickup-failed",
    "order.pickup-cancelled",
    "order.at-the-sorting-hub",
    "order.on-the-way-to-central-warehouse",
    "order.at-central-warehouse",
    "order.in-transit",
    "order.received-at-last-mile-hub",
    "order.assigned-for-delivery",
    "order.delivery-on-hold",
    "order.delivered",
    "order.partial-delivery",
    "order.delivery-failed",
    "order.returned",
    "order.paid-return",
    "order.exchange",
    "order.paid",
    "order.returned-at-sorting",
    "order.returned-in-transit",
    "order.returned-to-merchant",
)
