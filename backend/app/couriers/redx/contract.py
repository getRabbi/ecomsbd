"""The RedX Open API contract, as data.

Every host, path, header, field name, status string and delivery type in this
module is transcribed from **RedX's own developer documentation** —
``https://redx.com.bd/developer-api/``, the "Open API" and "Webhook" sections
of the RedX merchant site. The page renders in the browser from a script RedX
serves, so it was read from that script: the endpoint table, the status table
and the delivery-type table are data in it, not prose. The normalized reading
is committed at ``docs/providers/redx/CONTRACT.md``.

None of it comes from a community package, a blog post or memory. The earlier
version of this module recorded that no RedX documentation could be found; the
developer page is where RedX publishes it, and it is the only source used here.

Two things the documentation says that shape the whole integration:

*   **A parcel is created against a structured delivery area.** ``delivery_area``
    (a name) and ``delivery_area_id`` (an integer) are both *required* on
    create. RedX publishes the area list, and nowhere says a free-text address
    is resolved for you. So a RedX booking carries an area the seller picked
    from RedX's own list; ecomsbd never guesses one from an address, because a
    wrong area id is a parcel routed to the wrong hub, not an error.

*   **The credential is one bearer token per environment.** The merchant
    generates it on the developer page ("Request token"), separately for the
    sandbox and production hosts, and sends it as
    ``API-ACCESS-TOKEN: Bearer <token>`` on every call. There is no login call
    and no refresh contract.

What the documentation deliberately does **not** say, and is therefore not
assumed anywhere (see :data:`UNDOCUMENTED`): rate limits, the error body,
pagination, the token's lifetime, and what happens when the same
``merchant_invoice_id`` is sent twice — the idempotency answer.
"""

from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType
from typing import Final

__all__ = [
    "CANCEL_ENTITY_TYPE",
    "CANCEL_PROPERTY",
    "CANCEL_VALUE",
    "CONTRACT_VERIFIED_ON",
    "CREATE_PARCEL_REQUIRED",
    "DOCUMENTATION_SOURCE",
    "DOCUMENTATION_VERSION",
    "ENDPOINT_METHODS",
    "HEADER_AUTH",
    "LIVE_BASE_URL",
    "PROVIDER",
    "SAFE_TO_RETRY",
    "SANDBOX_BASE_URL",
    "UNDOCUMENTED",
    "WEBHOOK_PAYLOAD_FIELDS",
    "WEBHOOK_TOKEN_PARAM",
    "DeliveryType",
    "Endpoint",
    "RedxStatus",
    "bearer",
    "is_documented_status",
]

PROVIDER: Final = "redx"

#: The two environments the developer page lists, each with its own token.
#: The page gives ``openapi.redx.com.bd/v1.0.0-beta`` and
#: ``sandbox.redx.com.bd/v1.0.0-beta``; every example request is HTTPS.
LIVE_BASE_URL: Final = "https://openapi.redx.com.bd/v1.0.0-beta"
SANDBOX_BASE_URL: Final = "https://sandbox.redx.com.bd/v1.0.0-beta"

DOCUMENTATION_SOURCE: Final = (
    "RedX developer documentation, https://redx.com.bd/developer-api/ "
    "(Open API: Configuration, Track Parcel, Get Parcel Details, Create Parcel, "
    "Update Parcel, Get Areas, Get Pickup Stores, Pickup Store Details, "
    "Calculate Parcel Charge; Webhook: Callback URL Structure, Sample Request "
    "Format, Status Updates and Meanings, Delivery Type Reference Table). "
    "Normalized reading committed at docs/providers/redx/CONTRACT.md."
)
DOCUMENTATION_VERSION: Final = "Open API v1.0.0-beta (redx.com.bd/developer-api)"
CONTRACT_VERIFIED_ON: Final = "2026-09-23"

# ------------------------------------------------------------------- headers --

#: The one authentication header. Its value is ``Bearer <token>`` exactly as
#: every example on the developer page writes it.
HEADER_AUTH: Final = "API-ACCESS-TOKEN"


def bearer(token: str) -> str:
    """The ``API-ACCESS-TOKEN`` header value for a merchant token."""
    return f"Bearer {token}"


class Endpoint(StrEnum):
    """Documented paths, relative to the environment base URL.

    ``{reference}`` is the single path parameter where an endpoint has one.
    """

    TRACK_PARCEL = "/parcel/track/{reference}"
    PARCEL_INFO = "/parcel/info/{reference}"
    CREATE_PARCEL = "/parcel"
    UPDATE_PARCEL = "/parcels"
    AREAS = "/areas"
    PICKUP_STORES = "/pickup/stores"
    PICKUP_STORE_INFO = "/pickup/store/info/{reference}"
    CHARGE_CALCULATOR = "/charge/charge_calculator"

    def with_reference(self, reference: str) -> str:
        return str(self).replace("{reference}", reference)


ENDPOINT_METHODS: Final[MappingProxyType[Endpoint, str]] = MappingProxyType(
    {
        Endpoint.TRACK_PARCEL: "GET",
        Endpoint.PARCEL_INFO: "GET",
        Endpoint.CREATE_PARCEL: "POST",
        Endpoint.UPDATE_PARCEL: "PATCH",
        Endpoint.AREAS: "GET",
        Endpoint.PICKUP_STORES: "GET",
        Endpoint.PICKUP_STORE_INFO: "GET",
        Endpoint.CHARGE_CALCULATOR: "GET",
    }
)

#: Reads. Only these may be retried, and only a read is ever retried. The
#: parcel update (cancel) is a write and is sent exactly once.
SAFE_TO_RETRY: Final[frozenset[Endpoint]] = frozenset(
    {
        Endpoint.TRACK_PARCEL,
        Endpoint.PARCEL_INFO,
        Endpoint.AREAS,
        Endpoint.PICKUP_STORES,
        Endpoint.PICKUP_STORE_INFO,
        Endpoint.CHARGE_CALCULATOR,
    }
)

#: ``POST /pickup/store`` is documented too. ecomsbd never calls it: creating a
#: pickup location at RedX is the seller's decision to make in RedX's own
#: panel, not a side effect of connecting an account.

# ------------------------------------------------------------- create parcel --

#: The fields the documentation marks ``Required: Yes`` on create.
CREATE_PARCEL_REQUIRED: Final[tuple[str, ...]] = (
    "customer_name",
    "customer_phone",
    "delivery_area",
    "delivery_area_id",
    "customer_address",
    "cash_collection_amount",
    "parcel_weight",
    "value",
)

#: Optional create fields ecomsbd sends when it has a value for them.
#: ``type``, ``parcel_details_json`` and the ``is_closed_box`` that appears only
#: in the example request are never sent: ``type`` is for reverse shipments,
#: and the other two are shaped inconsistently between the field table and the
#: example (an object in one, an array in the other; absent from the table in
#: the second case), so there is no single documented shape to send.
CREATE_PARCEL_OPTIONAL_SENT: Final[tuple[str, ...]] = (
    "merchant_invoice_id",
    "instruction",
    "pickup_store_id",
)

#: ecomsbd's own ceilings, applied so an over-long value is trimmed at our
#: boundary rather than refused by RedX after a parcel might exist. RedX
#: publishes no length limits; these are conservative policy, not its claim.
FIELD_LIMITS: Final[MappingProxyType[str, int]] = MappingProxyType(
    {
        "customer_name": 100,
        "customer_address": 250,
        "instruction": 250,
        "merchant_invoice_id": 64,
        "delivery_area": 120,
    }
)

#: ecomsbd's merchant reference shape — the same conservative one Steadfast and
#: Pathao use, so one order's reference is valid at every provider. RedX
#: publishes no format rule for ``merchant_invoice_id``.
MERCHANT_INVOICE_PATTERN: Final = r"^[A-Za-z0-9_-]+$"

# -------------------------------------------------------------------- cancel --

#: ``PATCH /parcels`` changes one property of one parcel. The documentation's
#: only example is a cancellation, and that is the only use ecomsbd makes of it.
CANCEL_ENTITY_TYPE: Final = "parcel-tracking-id"
CANCEL_PROPERTY: Final = "status"
CANCEL_VALUE: Final = "cancelled"

# ------------------------------------------------------------------- statuses --


class RedxStatus(StrEnum):
    """Parcel statuses RedX publishes.

    The first eight are the "Status Updates and Meanings" table, verbatim, with
    RedX's own meaning beside each. ``PICKUP_PENDING`` is the one other status
    string the documentation contains: it is the ``status`` in the sample
    response of *Get Parcel Details*. RedX states no meaning for it.
    """

    READY_FOR_DELIVERY = "ready-for-delivery"
    DELIVERY_IN_PROGRESS = "delivery-in-progress"
    DELIVERED = "delivered"
    AGENT_HOLD = "agent-hold"
    AGENT_RETURNING = "agent-returning"
    RETURNED = "returned"
    AGENT_AREA_CHANGE = "agent-area-change"
    PAID = "paid"
    PICKUP_PENDING = "pickup-pending"


#: RedX's own words for each status, from the documentation's meaning column.
STATUS_MEANINGS: Final[MappingProxyType[RedxStatus, str]] = MappingProxyType(
    {
        RedxStatus.READY_FOR_DELIVERY: "Parcel received from merchants",
        RedxStatus.DELIVERY_IN_PROGRESS: "Parcels have been dispatched to rider",
        RedxStatus.DELIVERED: "Parcels delivered by rider",
        RedxStatus.AGENT_HOLD: "Parcels are on hold to agent",
        RedxStatus.AGENT_RETURNING: "Parcel return-in-progress",
        RedxStatus.RETURNED: "Parcels returned",
        RedxStatus.AGENT_AREA_CHANGE: "Area change requested & in progress",
        RedxStatus.PAID: "Parcel amount is paid",
        # Not in the meanings table. It appears only as the sample value of
        # `status` in the Get Parcel Details response, with no meaning stated.
        RedxStatus.PICKUP_PENDING: "",
    }
)

_STATUS_VALUES: Final[frozenset[str]] = frozenset(str(status) for status in RedxStatus)


def is_documented_status(value: str | None) -> bool:
    return value is not None and value in _STATUS_VALUES


class DeliveryType(StrEnum):
    """The "Delivery Type Reference Table", verbatim.

    Carried on a parcel (``parcel.delivery_type``) and on every webhook
    (``delivery_type``). It qualifies a status: ``delivered`` on a
    ``partial-delivery`` parcel is not the same outcome as ``delivered`` on a
    ``regular`` one, which is why the status mapping takes both.
    """

    REGULAR = "regular"
    REVERSE = "reverse"
    EXCHANGE_DELIVERY = "exchange-delivery"
    EXCHANGE_RETURN = "exchange-return"
    PARTIAL_DELIVERY = "partial-delivery"
    PARTIAL_RETURN = "partial-return"


DELIVERY_TYPE_MEANINGS: Final[MappingProxyType[DeliveryType, str]] = MappingProxyType(
    {
        DeliveryType.REGULAR: "Regular forward delivery",
        DeliveryType.REVERSE: "Regular reverse delivery",
        DeliveryType.EXCHANGE_DELIVERY: "Forward exchange parcel",
        DeliveryType.EXCHANGE_RETURN: "Reverse exchange parcel",
        DeliveryType.PARTIAL_DELIVERY: "Partial delivery parcel",
        DeliveryType.PARTIAL_RETURN: "Partial return parcel",
    }
)

# -------------------------------------------------------------------- webhook --

#: "Any required credentials should be included in the query parameters of the
#: URL", with ``https://example.com/callback?token=<token>`` as the example.
#: That is RedX's whole authentication story for callbacks: no signature
#: header, no HMAC. ecomsbd uses the documented example's parameter name.
WEBHOOK_TOKEN_PARAM: Final = "token"  # noqa: S105 - a query parameter *name*

#: The documented callback body, sent as ``application/json`` via POST.
WEBHOOK_PAYLOAD_FIELDS: Final[tuple[str, ...]] = (
    "tracking_number",
    "timestamp",
    "status",
    "message_en",
    "message_bn",
    "invoice_number",
    "delivery_type",
)

# ----------------------------------------------------------------- unknowns --

#: What the documentation is silent on. These are answers, not a backlog: each
#: one names a behaviour ecomsbd therefore does not rely on.
UNDOCUMENTED: Final[MappingProxyType[str, str]] = MappingProxyType(
    {
        "PROVIDER_CREATE_IDEMPOTENCY": (
            "What RedX does with a repeated merchant_invoice_id is not stated. "
            "An ambiguous create is BOOKING_UNKNOWN and is never re-sent."
        ),
        "LOOKUP_BY_MERCHANT_INVOICE": (
            "No endpoint finds a parcel by merchant_invoice_id, so an unconfirmed "
            "booking cannot be resolved automatically; a person resolves it."
        ),
        "ERROR_BODY_CONTRACT": "No error response shape is published.",
        "RATE_LIMIT_CONTRACT": "No rate limit is published.",
        "PAGINATION_CONTRACT": "No list endpoint documents pagination.",
        "TOKEN_LIFETIME": "Whether the API token expires is not stated.",
        "CREATE_FIELD_TYPES": (
            "The field table and the example request disagree on the JSON type "
            "of parcel_weight, value and pickup_store_id; the example's numbers "
            "are sent."
        ),
        "WEIGHT_UNIT_ON_CREATE": (
            "Create says 'appropriate units (e.g., kg, g)'; Get Parcel Details "
            "and the charge calculator both say grams, so grams are sent."
        ),
        "WEBHOOK_RESPONSE_CONTRACT": "What reply RedX expects, and whether it retries, is not stated.",
        "WEBHOOK_EVENT_ID": "Callbacks carry no event id; the body hash is the dedupe key.",
    }
)
