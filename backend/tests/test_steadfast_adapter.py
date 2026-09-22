"""The Steadfast client and adapter, against contract fixtures.

No network. Every test drives :class:`FakeSteadfastTransport`, which is also
what CI uses — the suite must never depend on Steadfast being up (brief
section 43).

The tests are grouped by the claim they defend, and the claims are the ones
that cost money when they are wrong:

* a create is sent **once**;
* an ambiguous failure is ``UNKNOWN``, never ``FAILED``;
* a bulk result is matched by invoice, never by position;
* a status string is mapped exactly, never by substring;
* ``delivered_approval_pending`` settles nothing.
"""

from __future__ import annotations

import json
import uuid

import pytest

from app.common.money import Money
from app.consignments.models import ConsignmentStatus
from app.couriers.adapter import BookingOutcome, BookingRequest
from app.couriers.capabilities import Capability
from app.couriers.steadfast.adapter import (
    SUPPORTED_CAPABILITIES,
    SteadfastAdapter,
    build_create_request,
    provider_cod_taka,
    to_provider_phone,
)
from app.couriers.steadfast.client import (
    StatusLookupKind,
    SteadfastClient,
    SteadfastConfig,
    SteadfastCredentials,
)
from app.couriers.steadfast.contract import (
    BULK_MAX_ITEMS_DOCUMENTED,
    SteadfastDeliveryStatus,
    validate_invoice,
)
from app.couriers.steadfast.dto import PaymentDetailResponse, PaymentListResponse
from app.couriers.steadfast.errors import (
    SteadfastAmbiguousError,
    SteadfastAuthError,
    SteadfastError,
    SteadfastErrorKind,
    SteadfastProtocolError,
    SteadfastUnavailableError,
)
from app.couriers.steadfast.mapping import (
    DELIVERY_STATUS_MAP,
    assert_map_is_complete,
    map_delivery_status,
)
from app.couriers.steadfast.transport import FakeSteadfastTransport
from tests.fixtures.steadfast import bodies

CREDS = SteadfastCredentials(api_key="test-api-key-value", secret_key="test-secret-key-value")


def _client(transport: FakeSteadfastTransport, **config: object) -> SteadfastClient:
    return SteadfastClient(
        transport,
        config=SteadfastConfig(**config),  # type: ignore[arg-type]
        # No real sleeping in tests; the retry loop's timing is not the subject.
        sleep=_no_sleep,
    )


async def _no_sleep(_seconds: float) -> None:
    return None


def _booking(
    *,
    reference: str = "ECB-SYNTH-0001",
    phone: str = "+8801700000000",
    cod_paisa: int = 106_000,
) -> BookingRequest:
    return BookingRequest(
        order_id=uuid.uuid4(),
        merchant_reference=reference,
        recipient_name="Test Recipient",
        recipient_phone_e164=phone,
        recipient_address="House 1, Road 1, Testpara, Dhaka-1200",
        cod_amount=Money(cod_paisa),
        item_description="One synthetic item",
        item_quantity=1,
    )


# --------------------------------------------------------------- contract --


def test_documented_status_map_is_complete() -> None:
    """Every documented status has an explicit mapping, and only those do."""
    assert_map_is_complete()
    assert len(DELIVERY_STATUS_MAP) == len(list(SteadfastDeliveryStatus)) == 11


def test_status_mapping_is_exact_never_a_substring() -> None:
    """``delivered_approval_pending`` must not be read as ``delivered``.

    The single most expensive mistake available here: a prefix match would
    settle a COD receivable on a parcel the courier has not approved, let alone
    paid for.
    """
    pending = map_delivery_status("delivered_approval_pending")
    settled = map_delivery_status("delivered")

    assert pending.canonical is ConsignmentStatus.OUT_FOR_DELIVERY
    assert pending.is_final is False
    assert pending.moves_money is False
    assert pending.is_approval_pending is True

    assert settled.canonical is ConsignmentStatus.DELIVERED
    assert settled.is_final is True
    assert settled.moves_money is True


@pytest.mark.parametrize(
    "status",
    [
        "delivered_approval_pending",
        "partial_delivered_approval_pending",
        "cancelled_approval_pending",
        "unknown_approval_pending",
    ],
)
def test_approval_pending_states_never_move_money(status: str) -> None:
    mapping = map_delivery_status(status)
    assert mapping.is_approval_pending is True
    assert mapping.is_final is False
    assert mapping.moves_money is False
    assert mapping.keep_polling is True


def test_partial_delivery_requires_quantity_resolution() -> None:
    """The status carries no quantities, so no quantity may be inferred."""
    mapping = map_delivery_status("partial_delivered")
    assert mapping.canonical is ConsignmentStatus.PARTIAL_DELIVERED
    assert mapping.needs_quantity_resolution is True


def test_unknown_future_status_is_stored_and_changes_nothing() -> None:
    mapping = map_delivery_status("teleported_to_customer")
    assert mapping.canonical is None
    assert mapping.is_documented is False
    assert mapping.moves_money is False
    # Still polled: an unrecognised state is not a terminal one.
    assert mapping.keep_polling is True
    assert mapping.provider_status == "teleported_to_customer"


def test_invoice_validation_matches_the_documented_character_set() -> None:
    validate_invoice("Aa12-das4")
    validate_invoice("a_sdfd-wq")
    validate_invoice("12366")
    for bad in ("has space", "has/slash", "has.dot", "", "x" * 41):
        with pytest.raises(ValueError):
            validate_invoice(bad)


def test_phone_is_transformed_at_the_boundary_only() -> None:
    assert to_provider_phone("+8801712345678") == "01712345678"
    assert to_provider_phone("01712345678") == "01712345678"
    with pytest.raises(ValueError):
        to_provider_phone("+12025550123")


def test_cod_amount_is_sent_in_taka_not_paisa() -> None:
    """Sending paisa would ask the courier to collect 100x the order value."""
    request = build_create_request(_booking(cod_paisa=106_000), "ECB-SYNTH-0001")
    assert request.cod_amount == 1060
    assert isinstance(request.cod_amount, int)
    assert request.as_payload()["cod_amount"] == 1060


def test_cod_payload_is_json_serializable() -> None:
    """A money field that cannot be encoded would fail at the transport."""
    payload = build_create_request(_booking(cod_paisa=105_050), "ECB-SYNTH-0001").as_payload()
    json.dumps(payload)


@pytest.mark.parametrize(
    ("paisa", "taka", "residual"),
    [
        (106_000, 1060, 0),
        (105_050, 1051, -50),  # half-up: 1050.50 -> 1051, 50 paisa more than owed
        (105_049, 1050, 49),  # 1050.49 -> 1050, 49 paisa short
        (0, 0, 0),
    ],
)
def test_cod_rounding_reports_the_residual_rather_than_dropping_it(
    paisa: int, taka: int, residual: int
) -> None:
    """A courier collects banknotes; the paisa remainder must stay visible.

    Silently rounding is how an unexplainable few-paisa reconciliation
    difference appears months later.
    """
    assert provider_cod_taka(Money(paisa)) == (taka, residual)


def test_create_payload_omits_absent_optionals() -> None:
    payload = build_create_request(_booking(), "ECB-SYNTH-0001").as_payload()
    assert set(payload) == {
        "invoice",
        "recipient_name",
        "recipient_phone",
        "recipient_address",
        "cod_amount",
        "item_description",
        "total_lot",
    }
    assert "recipient_email" not in payload


def test_redacted_payload_masks_the_phone() -> None:
    redacted = build_create_request(_booking(), "ECB-SYNTH-0001").redacted()
    assert redacted["recipient_phone"] == "01700****00"
    assert "01700000000" not in str(redacted)


# ------------------------------------------------------------ credentials --


async def test_validate_credentials_uses_balance_and_never_creates_a_parcel() -> None:
    transport = FakeSteadfastTransport().enqueue("GET", "/get_balance", body=bodies.BALANCE_OK)
    adapter = SteadfastAdapter(_client(transport))

    result = await adapter.validate_credentials(CREDS)

    assert result.valid is True
    assert result.account_label == "****alue"
    assert result.detected_capabilities == frozenset(SUPPORTED_CAPABILITIES)
    assert [call["path"] for call in transport.calls] == ["/get_balance"]


async def test_rejected_credentials_are_invalid() -> None:
    transport = FakeSteadfastTransport().enqueue("GET", "/get_balance", status_code=401, body="{}")
    adapter = SteadfastAdapter(_client(transport))

    result = await adapter.validate_credentials(CREDS)

    assert result.valid is False
    assert "rejected" in (result.message or "")


async def test_provider_outage_does_not_invalidate_credentials() -> None:
    """A 500 is our problem to wait out, not the seller's to re-type a key."""
    transport = FakeSteadfastTransport().always(status_code=500, body="{}")
    adapter = SteadfastAdapter(_client(transport, max_read_retries=0))

    result = await adapter.validate_credentials(CREDS)

    assert result.valid is False
    assert "not been marked wrong" in (result.message or "")


def test_credentials_never_render_their_secret() -> None:
    assert "test-api-key-value" not in repr(CREDS)
    assert "test-secret-key-value" not in f"{CREDS}"
    assert CREDS.masked_identifier == "****alue"


# ---------------------------------------------------------------- booking --


async def test_successful_create_returns_booked_with_provider_identity() -> None:
    transport = FakeSteadfastTransport().enqueue(
        "POST", "/create_order", body=bodies.create_ok(invoice="ECB-SYNTH-0001")
    )
    adapter = SteadfastAdapter(_client(transport))

    result = await adapter.create_consignment(CREDS, _booking(), "ECB-SYNTH-0001")

    assert result.outcome is BookingOutcome.BOOKED
    assert result.consignment is not None
    assert result.consignment.provider_consignment_id == "9900001"
    assert result.consignment.tracking_code == "TESTAA01"
    assert result.consignment.merchant_reference == "ECB-SYNTH-0001"
    assert result.consignment.raw_status == "in_review"
    # No charge is invented from the create response.
    assert result.consignment.charge is None


async def test_create_sends_the_documented_auth_headers_exactly_once() -> None:
    transport = FakeSteadfastTransport().enqueue("POST", "/create_order", body=bodies.CREATE_OK)
    adapter = SteadfastAdapter(_client(transport))

    await adapter.create_consignment(CREDS, _booking(), "ECB-SYNTH-0001")

    calls = transport.calls_to("POST", "/create_order")
    assert len(calls) == 1
    assert {"Api-Key", "Secret-Key", "Content-Type"} <= set(calls[0]["header_names"])
    assert calls[0]["idempotent"] is False


async def test_validation_rejection_is_a_safe_failure() -> None:
    """A 422 means the provider answered and refused. Nothing exists."""
    transport = FakeSteadfastTransport().enqueue(
        "POST", "/create_order", status_code=422, body=bodies.CREATE_VALIDATION_FAILURE
    )
    adapter = SteadfastAdapter(_client(transport))

    result = await adapter.create_consignment(CREDS, _booking(), "ECB-SYNTH-0001")

    assert result.outcome is BookingOutcome.FAILED
    assert result.is_ambiguous is False


async def test_read_timeout_on_create_is_ambiguous_not_failed() -> None:
    """The request may have arrived. This is the whole point of BOOKING_UNKNOWN."""
    transport = FakeSteadfastTransport().enqueue_error(
        "POST",
        "/create_order",
        SteadfastAmbiguousError("The courier did not answer in time"),
    )
    adapter = SteadfastAdapter(_client(transport))

    result = await adapter.create_consignment(CREDS, _booking(), "ECB-SYNTH-0001")

    assert result.outcome is BookingOutcome.UNKNOWN
    assert result.is_ambiguous is True


async def test_connect_failure_on_create_is_a_safe_failure() -> None:
    """Nothing was sent, so nothing was created, so a retry is safe."""
    transport = FakeSteadfastTransport().enqueue_error(
        "POST",
        "/create_order",
        SteadfastUnavailableError("Could not connect", reached_provider=False),
    )
    adapter = SteadfastAdapter(_client(transport))

    result = await adapter.create_consignment(CREDS, _booking(), "ECB-SYNTH-0001")

    assert result.outcome is BookingOutcome.FAILED


async def test_server_error_after_a_create_is_ambiguous() -> None:
    """A 500 on a write means the write may already have been applied."""
    transport = FakeSteadfastTransport().enqueue(
        "POST", "/create_order", status_code=500, body="{}"
    )
    adapter = SteadfastAdapter(_client(transport))

    result = await adapter.create_consignment(CREDS, _booking(), "ECB-SYNTH-0001")

    assert result.outcome is BookingOutcome.UNKNOWN


async def test_html_error_page_on_a_create_is_ambiguous() -> None:
    transport = FakeSteadfastTransport().enqueue(
        "POST",
        "/create_order",
        body=bodies.HTML_ERROR_PAGE,
        headers={"content-type": "text/html"},
    )
    adapter = SteadfastAdapter(_client(transport))

    result = await adapter.create_consignment(CREDS, _booking(), "ECB-SYNTH-0001")

    assert result.outcome is BookingOutcome.UNKNOWN


async def test_malformed_json_on_a_create_is_ambiguous() -> None:
    transport = FakeSteadfastTransport().enqueue(
        "POST", "/create_order", body='{"status": 200, "consignment":'
    )
    adapter = SteadfastAdapter(_client(transport))

    result = await adapter.create_consignment(CREDS, _booking(), "ECB-SYNTH-0001")

    assert result.outcome is BookingOutcome.UNKNOWN


async def test_body_status_not_200_is_a_failure_even_on_http_200() -> None:
    """Every documented response carries its status inside the body too."""
    transport = FakeSteadfastTransport().enqueue(
        "POST", "/create_order", status_code=200, body='{"status": 422, "message": "bad"}'
    )
    adapter = SteadfastAdapter(_client(transport))

    result = await adapter.create_consignment(CREDS, _booking(), "ECB-SYNTH-0001")

    assert result.outcome is BookingOutcome.FAILED


async def test_a_create_is_never_retried() -> None:
    transport = FakeSteadfastTransport().always(status_code=500, body="{}")
    adapter = SteadfastAdapter(_client(transport, max_read_retries=5))

    await adapter.create_consignment(CREDS, _booking(), "ECB-SYNTH-0001")

    assert len(transport.calls_to("POST", "/create_order")) == 1


async def test_locally_invalid_phone_fails_before_anything_is_sent() -> None:
    transport = FakeSteadfastTransport()
    adapter = SteadfastAdapter(_client(transport))

    result = await adapter.create_consignment(
        CREDS, _booking(phone="+12025550123"), "ECB-SYNTH-0001"
    )

    assert result.outcome is BookingOutcome.FAILED
    assert transport.calls == []


# ------------------------------------------------------------------ bulk --


async def test_bulk_all_success() -> None:
    transport = FakeSteadfastTransport().enqueue(
        "POST", "/create_order/bulk-order", body=bodies.BULK_ALL_SUCCESS
    )
    adapter = SteadfastAdapter(_client(transport))

    results = await adapter.create_bulk(
        CREDS, [_booking(reference=f"ECB-SYNTH-000{n}") for n in (1, 2, 3)]
    )

    assert [r.outcome for r in results] == [BookingOutcome.BOOKED] * 3
    assert [r.consignment.provider_consignment_id for r in results if r.consignment] == [
        "9900001",
        "9900002",
        "9900003",
    ]


async def test_one_failed_bulk_item_does_not_fail_the_others() -> None:
    transport = FakeSteadfastTransport().enqueue(
        "POST", "/create_order/bulk-order", body=bodies.BULK_MIXED
    )
    adapter = SteadfastAdapter(_client(transport))

    results = await adapter.create_bulk(
        CREDS, [_booking(reference=f"ECB-SYNTH-000{n}") for n in (1, 2, 3)]
    )

    assert [r.outcome for r in results] == [
        BookingOutcome.BOOKED,
        BookingOutcome.FAILED,
        BookingOutcome.BOOKED,
    ]


async def test_bulk_results_are_matched_by_invoice_not_by_position() -> None:
    """A reordered response must not attribute one order's parcel to another."""
    reordered = bodies.bulk_result(
        [
            {"invoice": "ECB-SYNTH-0003", "consignment_id": 9900003, "tracking_code": "TESTAA03"},
            {"invoice": "ECB-SYNTH-0001", "consignment_id": 9900001, "tracking_code": "TESTAA01"},
            {"invoice": "ECB-SYNTH-0002", "consignment_id": 9900002, "tracking_code": "TESTAA02"},
        ]
    )
    transport = FakeSteadfastTransport().enqueue("POST", "/create_order/bulk-order", body=reordered)
    adapter = SteadfastAdapter(_client(transport))

    results = await adapter.create_bulk(
        CREDS, [_booking(reference=f"ECB-SYNTH-000{n}") for n in (1, 2, 3)]
    )

    ids = [r.consignment.provider_consignment_id for r in results if r.consignment]
    assert ids == ["9900001", "9900002", "9900003"]


async def test_bulk_item_missing_from_the_response_is_ambiguous() -> None:
    """We sent it; the response does not mention it. It may exist."""
    partial = bodies.bulk_result(
        [{"invoice": "ECB-SYNTH-0001", "consignment_id": 9900001, "tracking_code": "TESTAA01"}]
    )
    transport = FakeSteadfastTransport().enqueue("POST", "/create_order/bulk-order", body=partial)
    adapter = SteadfastAdapter(_client(transport))

    results = await adapter.create_bulk(
        CREDS, [_booking(reference=f"ECB-SYNTH-000{n}") for n in (1, 2)]
    )

    assert results[0].outcome is BookingOutcome.BOOKED
    assert results[1].outcome is BookingOutcome.UNKNOWN


async def test_bulk_timeout_makes_every_sent_item_ambiguous_and_resends_nothing() -> None:
    """A bulk timeout is more dangerous than a single one (brief section 12)."""
    transport = FakeSteadfastTransport().enqueue_error(
        "POST",
        "/create_order/bulk-order",
        SteadfastAmbiguousError("The courier did not answer in time"),
    )
    adapter = SteadfastAdapter(_client(transport, max_read_retries=5))

    results = await adapter.create_bulk(
        CREDS, [_booking(reference=f"ECB-SYNTH-000{n}") for n in (1, 2, 3)]
    )

    assert [r.outcome for r in results] == [BookingOutcome.UNKNOWN] * 3
    assert len(transport.calls_to("POST", "/create_order/bulk-order")) == 1


async def test_bulk_success_status_with_null_id_is_not_treated_as_booked() -> None:
    """A booked parcel with nothing to look it up by is not a booked parcel."""
    odd = bodies.bulk_result(
        [{"invoice": "ECB-SYNTH-0001", "consignment_id": None, "status": "success"}]
    )
    transport = FakeSteadfastTransport().enqueue("POST", "/create_order/bulk-order", body=odd)
    adapter = SteadfastAdapter(_client(transport))

    results = await adapter.create_bulk(CREDS, [_booking(reference="ECB-SYNTH-0001")])

    assert results[0].outcome is BookingOutcome.FAILED


async def test_bulk_accepts_the_wrapped_error_shape() -> None:
    transport = FakeSteadfastTransport().enqueue(
        "POST", "/create_order/bulk-order", body=bodies.BULK_WRAPPED_ERROR
    )
    adapter = SteadfastAdapter(_client(transport))

    results = await adapter.create_bulk(CREDS, [_booking(reference="ECB-SYNTH-0001")])

    assert results[0].outcome is BookingOutcome.FAILED


async def test_bulk_refuses_more_than_the_documented_maximum() -> None:
    client = _client(FakeSteadfastTransport())
    with pytest.raises(ValueError, match="maximum"):
        await client.create_order_bulk(
            CREDS,
            [
                build_create_request(_booking(reference=f"ECB-{n}"), f"ECB-{n}")
                for n in range(BULK_MAX_ITEMS_DOCUMENTED + 1)
            ],
        )


def test_our_chunk_size_is_smaller_than_the_documented_ceiling() -> None:
    """Permission to send 500 is not a reason to (brief section 11)."""
    assert SteadfastConfig().bulk_chunk_size < BULK_MAX_ITEMS_DOCUMENTED


# ---------------------------------------------------------------- status --


@pytest.mark.parametrize("status", [str(value) for value in SteadfastDeliveryStatus])
async def test_every_documented_status_parses_and_maps(status: str) -> None:
    transport = FakeSteadfastTransport().enqueue(
        "GET", "/status_by_cid/9900001", body=bodies.STATUS_BODIES[status]
    )
    adapter = SteadfastAdapter(_client(transport))

    result = await adapter.get_status(CREDS, "9900001")

    assert not isinstance(result, type(None))
    assert result.raw_status == status  # type: ignore[union-attr]
    assert result.normalized_status is not None  # type: ignore[union-attr]


async def test_all_three_documented_lookup_forms_are_reachable() -> None:
    transport = (
        FakeSteadfastTransport()
        .enqueue("GET", "/status_by_cid/9900001", body=bodies.status_body("delivered"))
        .enqueue("GET", "/status_by_invoice/ECB-SYNTH-0001", body=bodies.status_body("pending"))
        .enqueue("GET", "/status_by_trackingcode/TESTAA01", body=bodies.status_body("hold"))
    )
    adapter = SteadfastAdapter(_client(transport))

    by_cid = await adapter.get_status_by(CREDS, "9900001", kind=StatusLookupKind.CONSIGNMENT_ID)
    by_invoice = await adapter.get_status_by(CREDS, "ECB-SYNTH-0001", kind=StatusLookupKind.INVOICE)
    by_tracking = await adapter.get_status_by(
        CREDS, "TESTAA01", kind=StatusLookupKind.TRACKING_CODE
    )

    assert by_cid.raw_status == "delivered"  # type: ignore[union-attr]
    assert by_invoice.raw_status == "pending"  # type: ignore[union-attr]
    assert by_tracking.raw_status == "hold"  # type: ignore[union-attr]


async def test_a_read_is_retried_but_a_write_is_not() -> None:
    transport = (
        FakeSteadfastTransport()
        .enqueue("GET", "/get_balance", status_code=503, body="{}")
        .enqueue("GET", "/get_balance", body=bodies.BALANCE_OK)
    )
    client = _client(transport, max_read_retries=2)

    result = await client.get_balance(CREDS)

    assert result.value == 123_456
    assert len(transport.calls_to("GET", "/get_balance")) == 2


async def test_status_lookup_never_claims_an_updated_timestamp() -> None:
    """The document gives no status timestamp; inventing one fakes a timeline."""
    transport = FakeSteadfastTransport().enqueue(
        "GET", "/status_by_cid/9900001", body=bodies.status_body("delivered")
    )
    adapter = SteadfastAdapter(_client(transport))

    result = await adapter.get_status(CREDS, "9900001")

    assert result.updated_at is None  # type: ignore[union-attr]


# --------------------------------------------------------------- balance --


async def test_balance_is_read_as_paisa() -> None:
    transport = FakeSteadfastTransport().enqueue("GET", "/get_balance", body=bodies.BALANCE_OK)
    adapter = SteadfastAdapter(_client(transport))

    balance = await adapter.get_balance(CREDS)

    assert balance == Money(123_456)


async def test_unreadable_balance_is_a_protocol_error_not_a_zero() -> None:
    transport = FakeSteadfastTransport().enqueue(
        "GET", "/get_balance", body='{"status": 200, "current_balance": "n/a"}'
    )
    client = _client(transport, max_read_retries=0)

    with pytest.raises(SteadfastProtocolError):
        await client.get_balance(CREDS)


# --------------------------------------------------------------- returns --


async def test_create_return_request_sends_exactly_one_reference() -> None:
    transport = FakeSteadfastTransport().enqueue(
        "POST", "/create_return_request", body=bodies.RETURN_REQUEST_CREATED
    )
    client = _client(transport)

    result = await client.create_return_request(CREDS, consignment_id="9900001", reason="Refused")

    assert result.value.provider_return_id == "4242"
    assert result.value.status == "pending"
    sent = transport.calls_to("POST", "/create_return_request")[0]["json"]
    assert sent == {"consignment_id": "9900001", "reason": "Refused"}


async def test_return_request_refuses_ambiguous_references() -> None:
    client = _client(FakeSteadfastTransport())
    with pytest.raises(ValueError, match="exactly one"):
        await client.create_return_request(CREDS, consignment_id="1", invoice="ECB-1")
    with pytest.raises(ValueError, match="exactly one"):
        await client.create_return_request(CREDS)


async def test_return_request_list_preserves_unknown_fields() -> None:
    """The lookup endpoints are path-only: unknown keys must survive."""
    transport = FakeSteadfastTransport().enqueue(
        "GET", "/get_return_requests", body=bodies.RETURN_REQUEST_LIST
    )
    client = _client(transport)

    result = await client.list_return_requests(CREDS)

    assert result.schema_is_undocumented is True
    first = result.value[0]
    assert first.status == "processing"
    assert first.raw["some_future_field"] == "preserved verbatim"
    assert "some_future_field" in first.observed_fields


# -------------------------------------------------------------- payments --


async def test_payments_list_parses_without_a_documented_schema() -> None:
    transport = FakeSteadfastTransport().enqueue("GET", "/payments", body=bodies.PAYMENTS_LIST)
    client = _client(transport)

    result = await client.list_payments(CREDS)
    parsed: PaymentListResponse = result.value

    assert result.schema_is_undocumented is True
    assert [p.provider_payment_id for p in parsed.payments] == ["55001", "55002"]
    assert parsed.payments[0].amount_paisa == 1_845_000
    assert parsed.has_declared_pagination is False


async def test_no_pagination_parameter_is_invented() -> None:
    transport = FakeSteadfastTransport().enqueue("GET", "/payments", body=bodies.PAYMENTS_LIST)
    client = _client(transport)

    await client.list_payments(CREDS)

    assert transport.calls_to("GET", "/payments")[0]["path"] == "/payments"


async def test_declared_pagination_is_reported() -> None:
    transport = FakeSteadfastTransport().enqueue(
        "GET", "/payments", body=bodies.PAYMENTS_LIST_PAGINATED
    )
    client = _client(transport)

    parsed: PaymentListResponse = (await client.list_payments(CREDS)).value

    assert parsed.has_declared_pagination is True
    assert parsed.current_page == 1
    assert parsed.last_page == 2


async def test_payment_detail_reads_consignment_identity_and_breakdown() -> None:
    transport = FakeSteadfastTransport().enqueue(
        "GET", "/payments/55001", body=bodies.PAYMENT_DETAIL_WITH_CONSIGNMENTS
    )
    client = _client(transport)

    detail: PaymentDetailResponse = (await client.get_payment(CREDS, "55001")).value

    assert detail.payment.provider_payment_id == "55001"
    assert len(detail.consignments) == 3
    assert all(row.has_identity for row in detail.consignments)

    first = detail.consignments[0]
    assert first.provider_consignment_id == "9900001"
    assert first.invoice == "ECB-SYNTH-0001"
    assert first.delivery_charge_paisa == 8_000
    assert first.cod_fee_paisa == 6_500
    assert first.has_charge_breakdown is True


async def test_payment_detail_without_a_breakdown_says_so() -> None:
    """An aggregate must not be split into a fabricated breakdown."""
    transport = FakeSteadfastTransport().enqueue(
        "GET", "/payments/55009", body=bodies.PAYMENT_DETAIL_AGGREGATE_ONLY
    )
    client = _client(transport)

    detail: PaymentDetailResponse = (await client.get_payment(CREDS, "55009")).value
    row = detail.consignments[0]

    assert row.has_charge_breakdown is False
    assert row.delivery_charge_paisa is None
    assert row.cod_fee_paisa is None
    assert row.cod_amount_paisa == 530_000
    assert row.payable_amount_paisa == 500_000


async def test_police_stations_parse_tolerantly() -> None:
    transport = FakeSteadfastTransport().enqueue(
        "GET", "/police_stations", body=bodies.POLICE_STATIONS
    )
    client = _client(transport)

    stations = (await client.list_police_stations(CREDS)).value

    assert [s.name for s in stations] == ["Dhanmondi", "Gulshan"]


# --------------------------------------------------------- capabilities ---


@pytest.mark.parametrize(
    "capability",
    [
        Capability.WEBHOOK,
        Capability.PRICE_QUOTE,
        Capability.CUSTOMER_STATS,
        Capability.CANCEL,
        Capability.LIST_STORES,
    ],
)
async def test_undocumented_capabilities_return_unavailable_not_an_error(
    capability: Capability,
) -> None:
    adapter = SteadfastAdapter(_client(FakeSteadfastTransport()))
    caps = await adapter.capabilities(CREDS)
    assert capability not in caps


async def test_quote_and_cancel_degrade_rather_than_raise() -> None:
    adapter = SteadfastAdapter(_client(FakeSteadfastTransport()))

    quote = await adapter.quote(CREDS, _booking())
    cancel = await adapter.cancel(CREDS, "9900001")
    stores = await adapter.list_stores(CREDS)

    for result in (quote, cancel, stores):
        assert bool(result) is False
        assert result.reason  # type: ignore[union-attr]


def test_webhook_verification_always_refuses() -> None:
    """No documented contract means no request may be accepted as verified."""
    adapter = SteadfastAdapter(_client(FakeSteadfastTransport()))
    assert adapter.verify_webhook({"x-signature": "anything"}, b"{}") is False
    assert adapter.parse_webhook({}, b'{"delivery_status": "delivered"}') == []


# ----------------------------------------------------------- error safety --


def test_ambiguous_errors_never_permit_a_write_retry() -> None:
    error = SteadfastAmbiguousError("lost")
    assert error.create_may_have_succeeded is True
    assert error.kind.is_safe_for_write_retry is False


def test_connect_failure_permits_a_write_retry() -> None:
    error = SteadfastUnavailableError("no connection", reached_provider=False)
    assert error.create_may_have_succeeded is False


def test_auth_failure_is_a_clean_failure() -> None:
    error = SteadfastAuthError("rejected", reached_provider=True)
    assert error.create_may_have_succeeded is False
    assert error.is_auth_failure is True


def test_error_context_carries_no_credential() -> None:
    error = SteadfastError(
        "failed",
        kind=SteadfastErrorKind.UNKNOWN,
        technical_context={"endpoint_status": 500},
    )
    rendered = str(error.as_log_context())
    assert "test-api-key-value" not in rendered
    assert "test-secret-key-value" not in rendered
