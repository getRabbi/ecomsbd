"""The Steadfast V1 contract, as data.

Every endpoint path, field name, length limit and status string in this module
was transcribed from the operator-supplied documentation
(``docs/providers/steadfast/CONTRACT.md``, sanitized source alongside it). None
of it comes from a community SDK, a blog post or memory.

The reason this is a module of constants rather than string literals sprinkled
through a client is that it makes one specific mistake visible in review: a
value that is not in the document has to be *added here* to be used, and this
file is the one a reviewer reads next to the documentation.

Two things this module deliberately does not contain:

*   any webhook path, header or signature scheme — the document has no webhook
    section at all (``WEBHOOK_CONTRACT_REQUIRED``);
*   any retry, rate-limit or idempotency semantics — the document states none,
    so ecomsbd provides its own and never claims the provider's.
"""

from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType
from typing import Final

__all__ = [
    "BULK_MAX_ITEMS_DOCUMENTED",
    "CONTRACT_VERIFIED_ON",
    "DEFAULT_BASE_URL",
    "DELIVERY_STATUS_DESCRIPTIONS",
    "DOCUMENTATION_SOURCE",
    "DOCUMENTATION_VERSION",
    "FIELD_LIMITS",
    "HEADER_API_KEY",
    "HEADER_CONTENT_TYPE",
    "HEADER_SECRET_KEY",
    "INVOICE_PATTERN",
    "DeliveryType",
    "Endpoint",
    "ReturnRequestStatus",
    "SteadfastDeliveryStatus",
    "is_documented_delivery_status",
    "is_documented_return_status",
    "validate_invoice",
]

#: Exactly as the document prints it.
DEFAULT_BASE_URL: Final = "https://portal.packzy.com/api/v1"

#: What was read, when. Both are reported by the manifest and the smoke tool.
DOCUMENTATION_SOURCE: Final = (
    "Steadfast Courier Limited — API Documentation V1 "
    "(operator-supplied Google Docs export; sanitized copy at "
    "docs/providers/steadfast/API_Documentation_V1.sanitized.html)"
)
DOCUMENTATION_VERSION: Final = "V1"
CONTRACT_VERIFIED_ON: Final = "2026-09-11"

# --------------------------------------------------------------- auth headers --

HEADER_API_KEY: Final = "Api-Key"
HEADER_SECRET_KEY: Final = "Secret-Key"
HEADER_CONTENT_TYPE: Final = "Content-Type"


class Endpoint(StrEnum):
    """Documented paths, relative to :data:`DEFAULT_BASE_URL`.

    Members whose docstring says *path-only* have a documented path and method
    and **no documented request or response schema**. They are still integrated,
    but as tolerant raw fetches — see :mod:`app.couriers.steadfast.dto`.
    """

    CREATE_ORDER = "/create_order"
    CREATE_ORDER_BULK = "/create_order/bulk-order"
    STATUS_BY_CID = "/status_by_cid/{reference}"
    STATUS_BY_INVOICE = "/status_by_invoice/{reference}"
    STATUS_BY_TRACKING_CODE = "/status_by_trackingcode/{reference}"
    GET_BALANCE = "/get_balance"
    CREATE_RETURN_REQUEST = "/create_return_request"
    #: path-only
    GET_RETURN_REQUEST = "/get_return_request/{reference}"
    #: path-only
    GET_RETURN_REQUESTS = "/get_return_requests"
    #: path-only
    PAYMENTS = "/payments"
    #: path-only
    PAYMENT_DETAIL = "/payments/{reference}"
    #: path-only
    POLICE_STATIONS = "/police_stations"

    def with_reference(self, reference: str) -> str:
        """Fill the single path parameter, if the endpoint has one.

        Deliberately not called ``format``: overriding ``str.format`` on a
        ``StrEnum`` narrows a builtin's signature, and a stray ``"{}".format``
        call elsewhere would then mean something different here.
        """
        return str(self).replace("{reference}", reference)


#: HTTP method per endpoint, from the document.
ENDPOINT_METHODS: Final[MappingProxyType[Endpoint, str]] = MappingProxyType(
    {
        Endpoint.CREATE_ORDER: "POST",
        Endpoint.CREATE_ORDER_BULK: "POST",
        Endpoint.STATUS_BY_CID: "GET",
        Endpoint.STATUS_BY_INVOICE: "GET",
        Endpoint.STATUS_BY_TRACKING_CODE: "GET",
        Endpoint.GET_BALANCE: "GET",
        Endpoint.CREATE_RETURN_REQUEST: "POST",
        Endpoint.GET_RETURN_REQUEST: "GET",
        Endpoint.GET_RETURN_REQUESTS: "GET",
        Endpoint.PAYMENTS: "GET",
        Endpoint.PAYMENT_DETAIL: "GET",
        Endpoint.POLICE_STATIONS: "GET",
    }
)

#: Endpoints whose response contract the document does not describe at all.
#: Their integration parses tolerantly and preserves every unknown key rather
#: than asserting a shape (master spec section 140; brief section 55).
PATH_ONLY_ENDPOINTS: Final[frozenset[Endpoint]] = frozenset(
    {
        Endpoint.GET_RETURN_REQUEST,
        Endpoint.GET_RETURN_REQUESTS,
        Endpoint.PAYMENTS,
        Endpoint.PAYMENT_DETAIL,
        Endpoint.POLICE_STATIONS,
    }
)

#: Read endpoints are safe to retry: they create nothing. Writes are not, and
#: the client refuses to retry them regardless of what a caller asks for.
SAFE_TO_RETRY: Final[frozenset[Endpoint]] = frozenset(
    endpoint for endpoint, method in ENDPOINT_METHODS.items() if method == "GET"
)

# --------------------------------------------------------------- create order --

#: Documented maximum for one bulk request. ecomsbd sends fewer — see
#: ``SteadfastConfig.bulk_chunk_size``.
BULK_MAX_ITEMS_DOCUMENTED: Final = 500

#: Documented length limits, in characters. ``None`` where the document gives a
#: field but no limit.
FIELD_LIMITS: Final[MappingProxyType[str, int | None]] = MappingProxyType(
    {
        "invoice": None,
        "recipient_name": 100,
        "recipient_phone": 11,
        "alternative_phone": 11,
        "recipient_email": None,
        "recipient_address": 250,
        "note": None,
        "item_description": None,
    }
)

#: "Must be Unique and can be alpha-numeric including hyphens and underscores."
#: Anchored, and deliberately *not* permissive: a merchant reference that would
#: be rejected must fail our own validation before a booking attempt is
#: persisted, not after a parcel may already exist.
INVOICE_PATTERN: Final = r"^[A-Za-z0-9_-]+$"

#: Practical ceiling on the invoice we generate. The document states no limit;
#: this is ecomsbd's own, chosen to stay well inside any plausible column.
INVOICE_MAX_LENGTH: Final = 40


class DeliveryType(StrEnum):
    """``delivery_type``: "0 = for home delivery, 1 = for Point Delivery/Steadfast Hub Pick Up"."""

    HOME_DELIVERY = "0"
    POINT_DELIVERY = "1"

    @property
    def wire_value(self) -> int:
        """The document types this field ``numeric``, so it goes out as an int."""
        return int(self.value)


def validate_invoice(invoice: str) -> None:
    """Raise ``ValueError`` if ``invoice`` breaks a documented constraint."""
    import re

    if not invoice:
        raise ValueError("Steadfast requires a non-empty invoice")
    if len(invoice) > INVOICE_MAX_LENGTH:
        raise ValueError(f"Invoice longer than ecomsbd's {INVOICE_MAX_LENGTH}-character limit")
    if not re.match(INVOICE_PATTERN, invoice):
        raise ValueError(
            "Steadfast invoices may contain only letters, digits, hyphens and underscores"
        )


# ------------------------------------------------------------ delivery status --


class SteadfastDeliveryStatus(StrEnum):
    """The eleven documented ``delivery_status`` values, verbatim.

    The list is closed *as of the supplied document*. A value that is not a
    member is not an error — providers add states — but it must never be
    guessed at. :func:`is_documented_delivery_status` is how calling code asks,
    and :mod:`app.couriers.steadfast.mapping` decides what an unknown one does.
    """

    PENDING = "pending"
    DELIVERED_APPROVAL_PENDING = "delivered_approval_pending"
    PARTIAL_DELIVERED_APPROVAL_PENDING = "partial_delivered_approval_pending"
    CANCELLED_APPROVAL_PENDING = "cancelled_approval_pending"
    UNKNOWN_APPROVAL_PENDING = "unknown_approval_pending"
    DELIVERED = "delivered"
    PARTIAL_DELIVERED = "partial_delivered"
    CANCELLED = "cancelled"
    HOLD = "hold"
    IN_REVIEW = "in_review"
    UNKNOWN = "unknown"

    @property
    def is_approval_pending(self) -> bool:
        """Whether the document says this state is awaiting admin approval.

        The four ``*_approval_pending`` descriptions say "waiting for admin
        approval"; the three settled ones say "balance added"/"balance
        updated". That difference is the documented basis for keeping
        approval-pending outcomes provisional (brief section 15).
        """
        return self in _APPROVAL_PENDING

    @property
    def balance_is_settled(self) -> bool:
        """Whether the document says the merchant balance has moved.

        True only for the three values whose description says so.
        """
        return self in (
            SteadfastDeliveryStatus.DELIVERED,
            SteadfastDeliveryStatus.PARTIAL_DELIVERED,
            SteadfastDeliveryStatus.CANCELLED,
        )

    @property
    def needs_provider_support(self) -> bool:
        """The two states whose own description says to contact support."""
        return self in (
            SteadfastDeliveryStatus.UNKNOWN,
            SteadfastDeliveryStatus.UNKNOWN_APPROVAL_PENDING,
        )


_APPROVAL_PENDING: Final[frozenset[SteadfastDeliveryStatus]] = frozenset(
    {
        SteadfastDeliveryStatus.DELIVERED_APPROVAL_PENDING,
        SteadfastDeliveryStatus.PARTIAL_DELIVERED_APPROVAL_PENDING,
        SteadfastDeliveryStatus.CANCELLED_APPROVAL_PENDING,
        SteadfastDeliveryStatus.UNKNOWN_APPROVAL_PENDING,
    }
)

#: The document's own description of each status, kept so support and the
#: seller-facing timeline can quote the provider rather than paraphrase it.
DELIVERY_STATUS_DESCRIPTIONS: Final[MappingProxyType[SteadfastDeliveryStatus, str]] = (
    MappingProxyType(
        {
            SteadfastDeliveryStatus.PENDING: "Consignment is not delivered or cancelled yet.",
            SteadfastDeliveryStatus.DELIVERED_APPROVAL_PENDING: (
                "Consignment is delivered but waiting for admin approval."
            ),
            SteadfastDeliveryStatus.PARTIAL_DELIVERED_APPROVAL_PENDING: (
                "Consignment is delivered partially and waiting for admin approval."
            ),
            SteadfastDeliveryStatus.CANCELLED_APPROVAL_PENDING: (
                "Consignment is cancelled and waiting for admin approval."
            ),
            SteadfastDeliveryStatus.UNKNOWN_APPROVAL_PENDING: (
                "Unknown Pending status. Need contact with the support team."
            ),
            SteadfastDeliveryStatus.DELIVERED: "Consignment is delivered and balance added.",
            SteadfastDeliveryStatus.PARTIAL_DELIVERED: (
                "Consignment is partially delivered and balance added."
            ),
            SteadfastDeliveryStatus.CANCELLED: "Consignment is cancelled and balance updated.",
            SteadfastDeliveryStatus.HOLD: "Consignment is held.",
            SteadfastDeliveryStatus.IN_REVIEW: "Order is placed and waiting to be reviewed.",
            SteadfastDeliveryStatus.UNKNOWN: (
                "Unknown status. Need contact with the support team."
            ),
        }
    )
)


def is_documented_delivery_status(value: str | None) -> bool:
    """Whether ``value`` is one of the eleven statuses the document lists.

    Exact, case-sensitive membership. Never a prefix test, a substring test or
    a fuzzy match: ``delivered_approval_pending`` starts with ``delivered`` and
    settling money on that basis is exactly the bug this function exists to
    make impossible (brief section 14).
    """
    if value is None:
        return False
    return value in _DELIVERY_STATUS_VALUES


_DELIVERY_STATUS_VALUES: Final[frozenset[str]] = frozenset(
    str(status) for status in SteadfastDeliveryStatus
)


# ------------------------------------------------------------- return request --


class ReturnRequestStatus(StrEnum):
    """The five documented return-request statuses, verbatim.

    > Status: 'pending', 'approved', 'processing', 'completed', 'cancelled'
    """

    PENDING = "pending"
    APPROVED = "approved"
    PROCESSING = "processing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (ReturnRequestStatus.COMPLETED, ReturnRequestStatus.CANCELLED)


_RETURN_STATUS_VALUES: Final[frozenset[str]] = frozenset(
    str(status) for status in ReturnRequestStatus
)


def is_documented_return_status(value: str | None) -> bool:
    """Exact membership in the five documented return statuses."""
    if value is None:
        return False
    return value in _RETURN_STATUS_VALUES


# ---------------------------------------------------------------- bulk result --

#: Per-item ``status`` values the bulk sample shows.
BULK_ITEM_SUCCESS: Final = "success"
BULK_ITEM_ERROR: Final = "error"

#: The document's body-level success marker. Present in every documented
#: response, alongside — not instead of — the HTTP status.
BODY_STATUS_OK: Final = 200
