"""The Pathao Courier merchant contract, as data.

Every path, field name, numeric code, header and event string in this module
was transcribed from **Pathao's own published integration source** — the
WooCommerce plugin released by Pathao's engineering organisation
(``github.com/pathao-eng/courier-woocommerce-plugin``), normalized in
``docs/providers/pathao/CONTRACT.md``. None of it comes from a community SDK,
a blog post or memory.

That distinction did real work here. The widely-repeated community contract for
Pathao says authentication is ``POST /aladdin/api/v1/issue-token`` with
``grant_type``, ``username`` and ``password``. Pathao's own current plugin
authenticates at ``POST /aladdin/api/v1/external/login`` with ``client_id`` and
``client_secret`` alone. Implementing from memory would have shipped a login
call that asks sellers for their Pathao account password and then fails.

What this module deliberately does **not** contain, because Pathao's published
source contains no evidence of it:

*   any status-lookup / order-info path. The plugin keeps parcel state current
    purely from webhooks and never polls. ``PATHAO_STATUS_LOOKUP_CONTRACT_REQUIRED``.
*   any price-quote, cancel, return, balance or payout path.
*   any rate-limit, pagination or idempotency semantics. Pathao states none, so
    ecomsbd provides its own and never claims the provider's.
"""

from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType
from typing import Final

__all__ = [
    "BULK_MAX_ITEMS_POLICY",
    "CONTRACT_VERIFIED_ON",
    "CREATE_ORDER_FIELDS",
    "DOCUMENTATION_SOURCE",
    "DOCUMENTATION_VERSION",
    "FIELD_LIMITS",
    "HEADER_WEBHOOK_SIGNATURE",
    "LIVE_BASE_URL",
    "SANDBOX_BASE_URL",
    "WEBHOOK_INTEGRATION_EVENT",
    "WEBHOOK_INTEGRATION_SECRET_HEADER",
    "WEBHOOK_INTEGRATION_SECRET_VALUE",
    "DeliveryType",
    "Endpoint",
    "ItemType",
    "PathaoEvent",
    "PathaoOrderStatus",
    "is_documented_event",
    "validate_merchant_order_id",
]

#: Exactly as Pathao's plugin selects them, by environment.
LIVE_BASE_URL: Final = "https://api-hermes.pathao.com"
SANDBOX_BASE_URL: Final = "https://courier-api-sandbox.pathao.com"

DOCUMENTATION_SOURCE: Final = (
    "Pathao Courier WooCommerce plugin, published by Pathao engineering at "
    "github.com/pathao-eng/courier-woocommerce-plugin (pathao-bridge.php, "
    "plugin-api.php, wc-order-list.php). Normalized reading committed at "
    "docs/providers/pathao/CONTRACT.md."
)
DOCUMENTATION_VERSION: Final = "aladdin/api/v1 (vendor plugin, main branch)"
CONTRACT_VERIFIED_ON: Final = "2026-09-17"

# ------------------------------------------------------------------- headers --

HEADER_AUTHORIZATION: Final = "Authorization"
HEADER_CONTENT_TYPE: Final = "Content-Type"
HEADER_ACCEPT: Final = "Accept"

#: Pathao's plugin sends a ``source`` header naming the integration. Harmless,
#: documented, and it makes ecomsbd traffic identifiable in Pathao's own logs
#: when a merchant opens a support ticket.
HEADER_SOURCE: Final = "source"
SOURCE_VALUE: Final = "ecomsbd"

#: The signature header on an inbound webhook. Pathao's plugin compares it for
#: **equality against the merchant's configured webhook secret** — it is a
#: shared secret, not an HMAC over the body. ecomsbd compares it in constant
#: time; that is a hardening of Pathao's scheme, not a different one.
HEADER_WEBHOOK_SIGNATURE: Final = "x-pathao-signature"

#: Pathao requires the receiver to echo a fixed integration constant on every
#: webhook response, and treats its absence as a failed integration. The value
#: is a protocol constant published in Pathao's plugin, not a secret of ours.
WEBHOOK_INTEGRATION_SECRET_HEADER: Final = "X-Pathao-Merchant-Webhook-Integration-Secret"
WEBHOOK_INTEGRATION_SECRET_VALUE: Final = "f3992ecc-59da-4cbe-a049-a13da2018d51"

#: The handshake Pathao sends when a merchant saves a webhook URL. It carries
#: no parcel, so it is acknowledged and never processed as an event.
WEBHOOK_INTEGRATION_EVENT: Final = "webhook_integration"


class Endpoint(StrEnum):
    """Paths Pathao's own plugin calls, relative to the environment base URL."""

    LOGIN = "/aladdin/api/v1/external/login"
    USER_SHORT_INFO = "/aladdin/api/v1/user/short-info"
    STORES = "/aladdin/api/v1/stores"
    CITY_LIST = "/aladdin/api/v1/countries/1/city-list"
    ZONE_LIST = "/aladdin/api/v1/cities/{reference}/zone-list"
    AREA_LIST = "/aladdin/api/v1/zones/{reference}/area-list"
    CREATE_ORDER = "/aladdin/api/v1/orders"
    CREATE_ORDER_BULK = "/aladdin/api/v1/orders/bulk"

    def with_reference(self, reference: str) -> str:
        """Fill the single path parameter, if the endpoint has one."""
        return str(self).replace("{reference}", reference)


ENDPOINT_METHODS: Final[MappingProxyType[Endpoint, str]] = MappingProxyType(
    {
        Endpoint.LOGIN: "POST",
        Endpoint.USER_SHORT_INFO: "GET",
        Endpoint.STORES: "GET",
        Endpoint.CITY_LIST: "GET",
        Endpoint.ZONE_LIST: "GET",
        Endpoint.AREA_LIST: "GET",
        Endpoint.CREATE_ORDER: "POST",
        Endpoint.CREATE_ORDER_BULK: "POST",
    }
)

#: Reads. Only these may be retried, and only a read is ever retried.
SAFE_TO_RETRY: Final[frozenset[Endpoint]] = frozenset(
    {
        Endpoint.USER_SHORT_INFO,
        Endpoint.STORES,
        Endpoint.CITY_LIST,
        Endpoint.ZONE_LIST,
        Endpoint.AREA_LIST,
    }
)

#: ecomsbd's own batch ceiling. Pathao publishes no maximum for
#: ``/orders/bulk``, so this is policy, not a provider claim: a failed
#: 50-item request is 50 parcels in an unknown state, and that is already a
#: bad morning.
BULK_MAX_ITEMS_POLICY: Final = 50

# ------------------------------------------------------------- create order --

#: The create payload Pathao's plugin builds, in its order. Starred entries are
#: always sent; the three location ids are sent **only when present**, which is
#: what makes free-text auto-address the primary path.
CREATE_ORDER_FIELDS: Final[tuple[str, ...]] = (
    "store_id",
    "merchant_order_id",
    "recipient_name",
    "recipient_phone",
    "recipient_secondary_phone",
    "recipient_address",
    "delivery_type",
    "item_type",
    "special_instruction",
    "item_quantity",
    "item_weight",
    "item_description",
    "recipient_city",
    "recipient_zone",
    "recipient_area",
    "amount_to_collect",
)

#: Fields Pathao's plugin coerces to a number before sending.
NUMERIC_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "store_id",
        "recipient_city",
        "recipient_zone",
        "recipient_area",
        "amount_to_collect",
        "item_quantity",
        "item_weight",
        "delivery_type",
        "item_type",
    }
)

#: Length ceilings. Pathao's merchant panel states 10–220 characters for an
#: address; the rest are ecomsbd's own conservative bounds, applied so an
#: over-long value is trimmed at our boundary rather than rejected after a
#: parcel may already exist.
FIELD_LIMITS: Final[MappingProxyType[str, int | None]] = MappingProxyType(
    {
        "recipient_name": 100,
        "recipient_address": 220,
        "item_description": 500,
        "special_instruction": 500,
        "merchant_order_id": 64,
    }
)

#: Pathao's panel states a 10-character minimum for an address. Refused here,
#: before a booking attempt is persisted, rather than by the provider after.
ADDRESS_MIN_LENGTH: Final = 10

#: ecomsbd's merchant reference shape. Pathao publishes no format rule for
#: ``merchant_order_id``; this is the same conservative shape Steadfast uses,
#: so one order's reference is valid at every provider.
MERCHANT_ORDER_ID_PATTERN: Final = r"^[A-Za-z0-9_-]+$"
MERCHANT_ORDER_ID_MAX_LENGTH: Final = 64


class DeliveryType(StrEnum):
    """Delivery types, with the wire codes Pathao's plugin offers."""

    NORMAL = "normal"
    ON_DEMAND = "on_demand"

    @property
    def wire_value(self) -> int:
        return _DELIVERY_TYPE_CODES[self]


_DELIVERY_TYPE_CODES: Final[MappingProxyType[DeliveryType, int]] = MappingProxyType(
    {DeliveryType.NORMAL: 48, DeliveryType.ON_DEMAND: 12}
)


class ItemType(StrEnum):
    """Item types, with the wire codes Pathao's plugin offers."""

    DOCUMENT = "document"
    PARCEL = "parcel"

    @property
    def wire_value(self) -> int:
        return _ITEM_TYPE_CODES[self]


_ITEM_TYPE_CODES: Final[MappingProxyType[ItemType, int]] = MappingProxyType(
    {ItemType.DOCUMENT: 1, ItemType.PARCEL: 2}
)


def validate_merchant_order_id(reference: str) -> None:
    """Refuse a reference Pathao could not carry back to us.

    The merchant reference is the only handle that exists before Pathao has
    answered, so it is the key to resolving an ambiguous create. A reference
    that cannot survive the round trip is refused here rather than discovered
    later.
    """
    import re

    if not reference:
        raise ValueError("A merchant order reference is required")
    if len(reference) > MERCHANT_ORDER_ID_MAX_LENGTH:
        raise ValueError(
            f"Merchant order reference is longer than {MERCHANT_ORDER_ID_MAX_LENGTH} characters"
        )
    if not re.match(MERCHANT_ORDER_ID_PATTERN, reference):
        raise ValueError("Merchant order reference may only contain letters, digits, - and _")


# ------------------------------------------------------------------- events --


class PathaoEvent(StrEnum):
    """The webhook events Pathao's plugin handles, verbatim."""

    ORDER_CREATED = "order.created"
    ORDER_UPDATED = "order.updated"
    PICKUP_REQUESTED = "order.pickup-requested"
    ASSIGNED_FOR_PICKUP = "order.assigned-for-pickup"
    PICKED = "order.picked"
    PICKUP_FAILED = "order.pickup-failed"
    PICKUP_CANCELLED = "order.pickup-cancelled"
    AT_THE_SORTING_HUB = "order.at-the-sorting-hub"
    IN_TRANSIT = "order.in-transit"
    RECEIVED_AT_LAST_MILE_HUB = "order.received-at-last-mile-hub"
    ASSIGNED_FOR_DELIVERY = "order.assigned-for-delivery"
    DELIVERED = "order.delivered"
    PARTIAL_DELIVERY = "order.partial-delivery"
    RETURNED = "order.returned"
    DELIVERY_FAILED = "order.delivery-failed"
    ON_HOLD = "order.on-hold"
    PAID_RETURN = "order.paid-return"
    EXCHANGED = "order.exchanged"
    PAID = "order.paid"


class PathaoOrderStatus(StrEnum):
    """The ``order_status`` labels Pathao's plugin stores per event.

    A webhook body may carry ``order_status`` directly; when it does not, the
    event name is mapped to one of these. Both forms are Pathao's own.
    """

    ORDER_CREATED = "Order_Created"
    ORDER_UPDATED = "Order_Updated"
    PICKUP_REQUESTED = "Pickup_Requested"
    ASSIGNED_FOR_PICKUP = "Assigned_for_Pickup"
    PICKED = "Picked"
    PICKUP_FAILED = "Pickup_Failed"
    PICKUP_CANCELLED = "Pickup_Cancelled"
    AT_THE_SORTING_HUB = "At_the_Sorting_HUB"
    IN_TRANSIT = "In_Transit"
    RECEIVED_AT_LAST_MILE_HUB = "Received_at_Last_Mile_HUB"
    ASSIGNED_FOR_DELIVERY = "Assigned_for_Delivery"
    DELIVERED = "Delivered"
    PARTIAL_DELIVERY = "Partial_Delivery"
    RETURN = "Return"
    DELIVERY_FAILED = "Delivery_Failed"
    ON_HOLD = "On_Hold"
    PAID_RETURN = "paid_return"
    EXCHANGE = "exchange"
    PAYMENT_INVOICE = "Payment_Invoice"


#: Event -> the status label Pathao's own plugin records for it. Copied exactly
#: from ``$orderEventsStatusMap``.
EVENT_STATUS_MAP: Final[MappingProxyType[PathaoEvent, PathaoOrderStatus]] = MappingProxyType(
    {
        PathaoEvent.ORDER_CREATED: PathaoOrderStatus.ORDER_CREATED,
        PathaoEvent.ORDER_UPDATED: PathaoOrderStatus.ORDER_UPDATED,
        PathaoEvent.PICKUP_REQUESTED: PathaoOrderStatus.PICKUP_REQUESTED,
        PathaoEvent.ASSIGNED_FOR_PICKUP: PathaoOrderStatus.ASSIGNED_FOR_PICKUP,
        PathaoEvent.PICKED: PathaoOrderStatus.PICKED,
        PathaoEvent.PICKUP_FAILED: PathaoOrderStatus.PICKUP_FAILED,
        PathaoEvent.PICKUP_CANCELLED: PathaoOrderStatus.PICKUP_CANCELLED,
        PathaoEvent.AT_THE_SORTING_HUB: PathaoOrderStatus.AT_THE_SORTING_HUB,
        PathaoEvent.IN_TRANSIT: PathaoOrderStatus.IN_TRANSIT,
        PathaoEvent.RECEIVED_AT_LAST_MILE_HUB: PathaoOrderStatus.RECEIVED_AT_LAST_MILE_HUB,
        PathaoEvent.ASSIGNED_FOR_DELIVERY: PathaoOrderStatus.ASSIGNED_FOR_DELIVERY,
        PathaoEvent.DELIVERED: PathaoOrderStatus.DELIVERED,
        PathaoEvent.PARTIAL_DELIVERY: PathaoOrderStatus.PARTIAL_DELIVERY,
        PathaoEvent.RETURNED: PathaoOrderStatus.RETURN,
        PathaoEvent.DELIVERY_FAILED: PathaoOrderStatus.DELIVERY_FAILED,
        PathaoEvent.ON_HOLD: PathaoOrderStatus.ON_HOLD,
        PathaoEvent.PAID_RETURN: PathaoOrderStatus.PAID_RETURN,
        PathaoEvent.EXCHANGED: PathaoOrderStatus.EXCHANGE,
        PathaoEvent.PAID: PathaoOrderStatus.PAYMENT_INVOICE,
    }
)

_EVENT_VALUES: Final[frozenset[str]] = frozenset(str(event) for event in PathaoEvent)
_STATUS_VALUES: Final[frozenset[str]] = frozenset(str(status) for status in PathaoOrderStatus)


def is_documented_event(value: str | None) -> bool:
    return value is not None and value in _EVENT_VALUES


def is_documented_status(value: str | None) -> bool:
    return value is not None and value in _STATUS_VALUES


def status_for_event(event: str | None) -> PathaoOrderStatus | None:
    """The status label Pathao records for an event, or ``None`` if unknown."""
    if event is None or event not in _EVENT_VALUES:
        return None
    return EVENT_STATUS_MAP[PathaoEvent(event)]
