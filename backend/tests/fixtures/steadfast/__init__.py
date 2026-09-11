"""Steadfast contract fixtures.

Every body here is derived from the operator-supplied V1 documentation
(``docs/providers/steadfast/CONTRACT.md``), with **synthetic customer data
only**. No real merchant, customer, phone number, address, consignment id or
tracking code appears anywhere in this package.

Two rules govern what may be added here:

*   A success fixture may only contain fields the document shows. Inventing a
    field and then writing a parser for it produces a test that proves nothing
    and a parser that will be wrong.
*   A fixture for a *path-only* endpoint (payments, payment detail, the two
    return lookups, police stations) is explicitly labelled as a plausible
    shape rather than a documented one, and the tests that use it assert the
    parser's **tolerance** — that unknown keys survive and absent keys stay
    ``None`` — never that a particular key exists.
"""

from tests.fixtures.steadfast.bodies import (
    BALANCE_OK,
    BULK_ALL_SUCCESS,
    BULK_MIXED,
    BULK_WRAPPED_ERROR,
    CREATE_OK,
    CREATE_VALIDATION_FAILURE,
    HTML_ERROR_PAGE,
    PAYMENT_DETAIL_AGGREGATE_ONLY,
    PAYMENT_DETAIL_WITH_CONSIGNMENTS,
    PAYMENTS_LIST,
    PAYMENTS_LIST_PAGINATED,
    POLICE_STATIONS,
    RETURN_REQUEST_CREATED,
    RETURN_REQUEST_LIST,
    STATUS_BODIES,
    status_body,
)

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
    "status_body",
]
