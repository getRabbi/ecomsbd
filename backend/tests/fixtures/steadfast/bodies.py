"""Response bodies, as JSON strings, exactly as the transport would see them.

Strings rather than dicts on purpose: the client's job includes deciding that a
body is unreadable, and a fixture that is already a dict skips the step most
likely to be wrong.
"""

from __future__ import annotations

import json
from typing import Any

from app.couriers.steadfast.contract import SteadfastDeliveryStatus

__all__ = [
    "BALANCE_OK",
    "BULK_ALL_SUCCESS",
    "BULK_MIXED",
    "BULK_WRAPPED_ERROR",
    "CREATE_OK",
    "CREATE_VALIDATION_FAILURE",
    "HTML_ERROR_PAGE",
    "PAYMENTS_LIST",
    "PAYMENTS_LIST_PAGINATED",
    "PAYMENT_DETAIL_AGGREGATE_ONLY",
    "PAYMENT_DETAIL_WITH_CONSIGNMENTS",
    "POLICE_STATIONS",
    "RETURN_REQUEST_CREATED",
    "RETURN_REQUEST_LIST",
    "STATUS_BODIES",
    "bulk_result",
    "create_ok",
    "status_body",
]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


# --------------------------------------------------------------- create ----
# Shaped exactly like the document's sample, with synthetic values.


def create_ok(
    *,
    invoice: str = "ECB-SYNTH-0001",
    consignment_id: int = 9900001,
    tracking_code: str = "TESTAA01",
    cod_amount: int | str = 1060,
    status: str = "in_review",
) -> str:
    return _json(
        {
            "status": 200,
            "message": "Consignment has been created successfully.",
            "consignment": {
                "consignment_id": consignment_id,
                "invoice": invoice,
                "tracking_code": tracking_code,
                "recipient_name": "Test Recipient",
                "recipient_phone": "01700000000",
                "recipient_address": "House 1, Road 1, Testpara, Dhaka-1200",
                "cod_amount": cod_amount,
                "status": status,
                "note": "Synthetic fixture",
                "created_at": "2026-09-11T07:05:31.000000Z",
                "updated_at": "2026-09-11T07:05:31.000000Z",
            },
        }
    )


CREATE_OK = create_ok()

#: The document describes no error body. This is a *minimal* 422 body — a body
#: status and nothing else — because that is all we can justify asserting.
#: Tests using it check that a 422 is a safe failure, never that a particular
#: error field was read.
CREATE_VALIDATION_FAILURE = _json({"status": 422, "message": "The given data was invalid."})


# ----------------------------------------------------------------- bulk ----


def bulk_result(entries: list[dict[str, Any]], *, wrapped: bool = False) -> str:
    """Build a bulk body in either documented shape."""
    rows = [
        {
            "invoice": entry["invoice"],
            "recipient_name": "Test Recipient",
            "recipient_address": "House 1, Road 1, Testpara, Dhaka-1200",
            "recipient_phone": "01700000000",
            "cod_amount": entry.get("cod_amount", "0.00"),
            "note": None,
            "consignment_id": entry.get("consignment_id"),
            "tracking_code": entry.get("tracking_code"),
            "status": entry.get("status", "success"),
        }
        for entry in entries
    ]
    return _json({"data": rows}) if wrapped else _json(rows)


BULK_ALL_SUCCESS = bulk_result(
    [
        {"invoice": "ECB-SYNTH-0001", "consignment_id": 9900001, "tracking_code": "TESTAA01"},
        {"invoice": "ECB-SYNTH-0002", "consignment_id": 9900002, "tracking_code": "TESTAA02"},
        {"invoice": "ECB-SYNTH-0003", "consignment_id": 9900003, "tracking_code": "TESTAA03"},
    ]
)

#: Two booked, one refused. The refused entry carries the document's own error
#: shape: null ids and ``status: "error"``.
BULK_MIXED = bulk_result(
    [
        {"invoice": "ECB-SYNTH-0001", "consignment_id": 9900001, "tracking_code": "TESTAA01"},
        {
            "invoice": "ECB-SYNTH-0002",
            "consignment_id": None,
            "tracking_code": None,
            "status": "error",
        },
        {"invoice": "ECB-SYNTH-0003", "consignment_id": 9900003, "tracking_code": "TESTAA03"},
    ]
)

#: The same mixed result in the document's *other* shape — wrapped in ``data``.
BULK_WRAPPED_ERROR = bulk_result(
    [
        {
            "invoice": "ECB-SYNTH-0001",
            "consignment_id": None,
            "tracking_code": None,
            "status": "error",
        },
    ],
    wrapped=True,
)


# --------------------------------------------------------------- status ----


def status_body(status: str) -> str:
    return _json({"status": 200, "delivery_status": status})


#: One body per documented status. The mapping test iterates this, so a status
#: added to the contract without a fixture fails rather than being skipped.
STATUS_BODIES: dict[str, str] = {
    str(value): status_body(str(value)) for value in SteadfastDeliveryStatus
}


# -------------------------------------------------------------- balance ----

BALANCE_OK = _json({"status": 200, "current_balance": 1234.56})


# -------------------------------------------------------------- returns ----

#: The create-return response is documented as a value table. This is that
#: table as an object, which is the only reading available.
RETURN_REQUEST_CREATED = _json(
    {
        "id": 4242,
        "user_id": 77,
        "consignment_id": 9900001,
        "reason": None,
        "status": "pending",
        "created_at": "2026-09-11T23:11:45.000000Z",
        "updated_at": "2026-09-11T23:11:45.000000Z",
    }
)

#: PATH-ONLY endpoint: no schema is documented. This is a *plausible* shape, and
#: the tests that use it assert tolerance, not field presence.
RETURN_REQUEST_LIST = _json(
    {
        "data": [
            {
                "id": 4242,
                "consignment_id": 9900001,
                "status": "processing",
                "reason": "Customer refused",
                "created_at": "2026-09-11T23:11:45.000000Z",
                "updated_at": "2026-09-12T04:00:00.000000Z",
                "some_future_field": "preserved verbatim",
            },
            {
                "id": 4243,
                "consignment_id": 9900002,
                "status": "completed",
                "reason": None,
                "created_at": "2026-09-10T10:00:00.000000Z",
                "updated_at": "2026-09-12T09:30:00.000000Z",
            },
        ]
    }
)


# ------------------------------------------------------------- payments ----
#
# PATH-ONLY endpoints. The document gives `/payments` and `/payments/{id}` with
# no schema whatsoever. Everything below is a plausible shape used to prove the
# parser is tolerant; no test asserts that Steadfast really uses these names.

PAYMENTS_LIST = _json(
    {
        "data": [
            {
                "id": 55001,
                "amount": "18450.00",
                "status": "paid",
                "paid_at": "2026-09-10T12:00:00.000000Z",
                "reference": "PAY-SYNTH-55001",
                "consignment_count": 3,
            },
            {
                "id": 55002,
                "amount": "9200.50",
                "status": "paid",
                "paid_at": "2026-09-11T12:00:00.000000Z",
                "reference": "PAY-SYNTH-55002",
                "consignment_count": 2,
            },
        ]
    }
)

#: A Laravel-style paginator. The sync job walks pages only when the response
#: declares them like this; it never invents a ``?page=`` parameter.
PAYMENTS_LIST_PAGINATED = _json(
    {
        "current_page": 1,
        "last_page": 2,
        "next_page_url": "https://portal.packzy.com/api/v1/payments?page=2",
        "data": [
            {
                "id": 55003,
                "amount": "1000.00",
                "status": "paid",
                "paid_at": "2026-09-12T12:00:00.000000Z",
                "reference": "PAY-SYNTH-55003",
            }
        ],
    }
)

PAYMENT_DETAIL_WITH_CONSIGNMENTS = _json(
    {
        "id": 55001,
        "amount": "18450.00",
        "status": "paid",
        "paid_at": "2026-09-10T12:00:00.000000Z",
        "reference": "PAY-SYNTH-55001",
        "consignments": [
            {
                "consignment_id": 9900001,
                "invoice": "ECB-SYNTH-0001",
                "tracking_code": "TESTAA01",
                "cod_amount": "6500.00",
                "delivery_charge": "80.00",
                "cod_fee": "65.00",
                "payable_amount": "6355.00",
                "status": "delivered",
                "delivered_at": "2026-09-09T14:20:00.000000Z",
            },
            {
                "consignment_id": 9900002,
                "invoice": "ECB-SYNTH-0002",
                "tracking_code": "TESTAA02",
                "cod_amount": "12000.00",
                "delivery_charge": "120.00",
                "cod_fee": "120.00",
                "payable_amount": "11760.00",
                "status": "delivered",
                "delivered_at": "2026-09-09T16:05:00.000000Z",
            },
            {
                "consignment_id": 9900003,
                "invoice": "ECB-SYNTH-0003",
                "tracking_code": "TESTAA03",
                "cod_amount": "0.00",
                "return_charge": "60.00",
                "payable_amount": "-60.00",
                "status": "cancelled",
            },
        ],
    }
)

#: The same payment with **no** itemised deductions — only a net figure. The
#: gap must become one UNKNOWN_DEDUCTION adjustment, never a fabricated split
#: into delivery charge and COD fee.
PAYMENT_DETAIL_AGGREGATE_ONLY = _json(
    {
        "id": 55009,
        "amount": "5000.00",
        "status": "paid",
        "paid_at": "2026-09-10T12:00:00.000000Z",
        "reference": "PAY-SYNTH-55009",
        "consignments": [
            {
                "consignment_id": 9900010,
                "invoice": "ECB-SYNTH-0010",
                "cod_amount": "5300.00",
                "payable_amount": "5000.00",
            }
        ],
    }
)


# ------------------------------------------------------- police stations ----

POLICE_STATIONS = _json(
    {
        "data": [
            {"id": 1, "name": "Dhanmondi", "district": "Dhaka"},
            {"id": 2, "name": "Gulshan", "district": "Dhaka"},
        ]
    }
)


# ---------------------------------------------------------- failure bodies --

#: What a load balancer or captive portal returns. A 200 carrying this is not
#: success, and for a write it is ambiguous rather than failed.
HTML_ERROR_PAGE = (
    "<!DOCTYPE html>\n<html><head><title>502 Bad Gateway</title></head>"
    "<body><h1>502 Bad Gateway</h1></body></html>"
)
