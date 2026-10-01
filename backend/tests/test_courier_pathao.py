"""Pathao adapter, client, mapping and webhook.

Every test here defends a claim that costs a seller real money if it breaks.
The ones that matter most:

* an ambiguous create becomes ``UNKNOWN``, never ``FAILED``, and is never
  re-sent — a duplicate parcel is charged twice;
* a status Pathao publishes but does not explain never settles money;
* a webhook is verified against *this shop's* secret before its body is read,
  and an unsigned one is refused.
"""

from __future__ import annotations

import json
import uuid

import pytest

from app.common.money import BDT, Money
from app.consignments.models import ConsignmentStatus
from app.couriers.adapter import BookingOutcome, BookingRequest, Unavailable
from app.couriers.capabilities import Capability, load_manifest
from app.couriers.http import (
    FakeProviderTransport,
    ProviderAmbiguousError,
    ProviderUnavailableError,
)
from app.couriers.pathao.adapter import (
    PathaoAdapter,
    build_create_payload,
    provider_cod_taka,
    to_provider_phone,
    to_provider_weight_kg,
)
from app.couriers.pathao.client import PathaoClient, PathaoConfig, PathaoCredentials
from app.couriers.pathao.contract import Endpoint, PathaoOrderStatus
from app.couriers.pathao.mapping import assert_map_is_complete, map_event, map_order_status
from app.couriers.pathao.webhooks import (
    PathaoWebhookVerifier,
    is_integration_handshake,
    parse_pathao_events,
)

# Each test here is about a courier a shop may use, so the kill switches are on.
pytestmark = pytest.mark.usefixtures("courier_flags_on")

CLIENT_ID = "pathao-client-id-0001"
CLIENT_SECRET = "pathao-client-secret-0001"
STORE_ID = "12345"

LOGIN_BODY = json.dumps(
    {
        "access_token": "tok-abc-123",
        "refresh_token": "ref-abc-123",
        "expires_in": 3600,
        "token_type": "Bearer",
    }
)


def _creds(*, sandbox: bool = False) -> PathaoCredentials:
    return PathaoCredentials(client_id=CLIENT_ID, client_secret=CLIENT_SECRET, sandbox=sandbox)


def _transport() -> FakeProviderTransport:
    transport = FakeProviderTransport(provider="pathao")
    transport.always("POST", str(Endpoint.LOGIN), body=LOGIN_BODY)
    return transport


def _adapter(transport: FakeProviderTransport) -> PathaoAdapter:
    return PathaoAdapter(PathaoClient(transport, config=PathaoConfig()))


def _booking(**overrides) -> BookingRequest:
    base = {
        "order_id": uuid.uuid4(),
        "merchant_reference": "ECB-1001",
        "recipient_name": "Rahim Uddin",
        "recipient_phone_e164": "+8801712345678",
        "recipient_address": "House 12, Road 5, Dhanmondi, Dhaka",
        "cod_amount": Money(paisa=105000, currency=BDT),
        "item_description": "Cotton shirt",
        "item_quantity": 2,
        "weight_grams": 1200,
        "store_reference": STORE_ID,
    }
    base.update(overrides)
    return BookingRequest(**base)


# --------------------------------------------------------------- mapping --


def test_every_published_status_is_mapped() -> None:
    assert_map_is_complete()


def test_delivered_is_final_and_moves_money() -> None:
    mapping = map_order_status(str(PathaoOrderStatus.DELIVERED))
    assert mapping.canonical is ConsignmentStatus.DELIVERED
    assert mapping.is_final
    assert mapping.moves_money
    assert not mapping.keep_polling


def test_partial_delivery_needs_a_person_to_supply_quantities() -> None:
    mapping = map_order_status(str(PathaoOrderStatus.PARTIAL_DELIVERY))
    assert mapping.canonical is ConsignmentStatus.PARTIAL_DELIVERED
    assert mapping.needs_quantity_resolution


def test_delivery_failed_is_not_terminal() -> None:
    """A failed attempt is followed by another attempt or a return.

    Closing the parcel here would strand its COD.
    """
    mapping = map_order_status(str(PathaoOrderStatus.DELIVERY_FAILED))
    assert mapping.canonical is ConsignmentStatus.IN_TRANSIT
    assert not mapping.is_final
    assert not mapping.moves_money
    assert mapping.keep_polling


def test_pickup_cancelled_does_not_cancel_the_order() -> None:
    """CANCELLED is terminal and touches stock. Pathao states no such thing."""
    mapping = map_order_status(str(PathaoOrderStatus.PICKUP_CANCELLED))
    assert mapping.canonical is not ConsignmentStatus.CANCELLED
    assert not mapping.is_final


def test_paid_return_does_not_settle_as_returned() -> None:
    """`Return` is the documented return outcome; `paid_return` explains nothing."""
    mapping = map_order_status(str(PathaoOrderStatus.PAID_RETURN))
    assert mapping.canonical is not ConsignmentStatus.RETURNED
    assert not mapping.is_final
    assert not mapping.moves_money


def test_payment_invoice_makes_no_parcel_claim() -> None:
    mapping = map_order_status(str(PathaoOrderStatus.PAYMENT_INVOICE))
    assert mapping.canonical is None
    assert mapping.is_documented


def test_unknown_status_changes_nothing_and_is_not_an_error() -> None:
    mapping = map_order_status("Teleported_To_Mars")
    assert mapping.canonical is None
    assert not mapping.is_documented
    assert not mapping.moves_money
    assert mapping.keep_polling
    assert mapping.provider_status == "Teleported_To_Mars"


def test_event_names_map_through_to_statuses() -> None:
    assert map_event("order.delivered").canonical is ConsignmentStatus.DELIVERED
    assert map_event("order.picked").canonical is ConsignmentStatus.PICKED_UP
    assert not map_event("order.invented-by-us").is_documented


# ------------------------------------------------------ boundary transforms --


def test_phone_is_converted_to_the_national_form() -> None:
    assert to_provider_phone("+8801712345678") == "01712345678"


def test_an_invalid_phone_is_refused_before_anything_is_sent() -> None:
    with pytest.raises(ValueError):
        to_provider_phone("+12025550123")


def test_cod_is_sent_in_taka_and_the_residual_is_returned() -> None:
    taka, residual = provider_cod_taka(Money(paisa=105050, currency=BDT))
    assert taka == 1051
    # Not dropped: the booking records what the courier was actually asked for.
    assert residual == 105050 - 105100


def test_weight_converts_grams_to_kilograms() -> None:
    assert to_provider_weight_kg(1200) == 1.2
    # A required field with no order data gets ecomsbd's default, not a crash.
    assert to_provider_weight_kg(None) == 0.5
    assert to_provider_weight_kg(0) == 0.5


# ------------------------------------------------------------ create payload --


def test_create_payload_omits_location_ids_so_auto_address_is_the_path() -> None:
    payload = build_create_payload(_booking(), "ECB-1001", store_id=STORE_ID)
    assert "recipient_city" not in payload
    assert "recipient_zone" not in payload
    assert "recipient_area" not in payload
    assert payload["recipient_address"] == "House 12, Road 5, Dhanmondi, Dhaka"


def test_create_payload_uses_the_published_wire_codes() -> None:
    payload = build_create_payload(_booking(), "ECB-1001", store_id=STORE_ID)
    assert payload["delivery_type"] == 48
    assert payload["item_type"] == 2
    assert payload["store_id"] == 12345
    assert payload["amount_to_collect"] == 1050
    assert payload["item_weight"] == 1.2
    assert payload["recipient_phone"] == "01712345678"


def test_booking_without_a_store_is_refused_locally() -> None:
    with pytest.raises(ValueError, match="pickup store"):
        build_create_payload(_booking(), "ECB-1001", store_id="")


def test_a_too_short_address_is_refused_before_a_parcel_can_exist() -> None:
    with pytest.raises(ValueError, match="address"):
        build_create_payload(_booking(recipient_address="Dhaka"), "ECB-1001", store_id=STORE_ID)


# ----------------------------------------------------------------- client --


@pytest.mark.asyncio
async def test_one_login_serves_many_calls() -> None:
    """A burst of bookings for one shop must produce one login, not one each."""
    transport = _transport()
    transport.always("GET", str(Endpoint.USER_SHORT_INFO), body=json.dumps({"data": {}}))
    client = PathaoClient(transport, config=PathaoConfig())

    await client.user_short_info(_creds())
    await client.user_short_info(_creds())

    assert len(transport.calls_to("POST", str(Endpoint.LOGIN))) == 1


@pytest.mark.asyncio
async def test_sandbox_credentials_route_to_the_sandbox_host() -> None:
    transport = _transport()
    transport.always("GET", str(Endpoint.USER_SHORT_INFO), body=json.dumps({"data": {}}))
    client = PathaoClient(transport, config=PathaoConfig())

    await client.user_short_info(_creds(sandbox=True))

    assert "courier-api-sandbox.pathao.com" in transport.calls[-1]["url"]


@pytest.mark.asyncio
async def test_a_successful_create_returns_the_consignment_id() -> None:
    transport = _transport()
    transport.enqueue(
        "POST",
        str(Endpoint.CREATE_ORDER),
        status_code=201,
        body=json.dumps(
            {
                "data": {
                    "consignment_id": "DA200101",
                    "merchant_order_id": "ECB-1001",
                    "delivery_fee": 70,
                    "order_status": "Order_Created",
                }
            }
        ),
    )
    result = await _adapter(transport).create_consignment(_creds(), _booking(), "ECB-1001")

    assert result.outcome is BookingOutcome.BOOKED
    assert result.consignment is not None
    assert result.consignment.provider_consignment_id == "DA200101"
    # A quoted fee is not a settled cost.
    assert result.consignment.charge is None


@pytest.mark.asyncio
async def test_a_create_is_sent_exactly_once() -> None:
    transport = _transport()
    transport.enqueue(
        "POST",
        str(Endpoint.CREATE_ORDER),
        status_code=500,
        body=json.dumps({"message": "server error"}),
    )
    result = await _adapter(transport).create_consignment(_creds(), _booking(), "ECB-1001")

    assert result.outcome is BookingOutcome.UNKNOWN
    assert len(transport.calls_to("POST", str(Endpoint.CREATE_ORDER))) == 1


@pytest.mark.asyncio
async def test_a_lost_answer_is_unknown_not_failed() -> None:
    """The whole point of the enum. FAILED here would ship a second parcel."""
    transport = _transport()
    transport.enqueue_error(
        "POST",
        str(Endpoint.CREATE_ORDER),
        ProviderAmbiguousError("read timeout", provider="pathao"),
    )
    result = await _adapter(transport).create_consignment(_creds(), _booking(), "ECB-1001")

    assert result.outcome is BookingOutcome.UNKNOWN


@pytest.mark.asyncio
async def test_a_request_that_never_left_is_a_clean_failure() -> None:
    transport = _transport()
    transport.enqueue_error(
        "POST",
        str(Endpoint.CREATE_ORDER),
        ProviderUnavailableError("could not connect", provider="pathao", reached_provider=False),
    )
    result = await _adapter(transport).create_consignment(_creds(), _booking(), "ECB-1001")

    assert result.outcome is BookingOutcome.FAILED


@pytest.mark.asyncio
async def test_a_2xx_with_no_consignment_id_is_ambiguous() -> None:
    """Pathao accepted it and told us nothing. The parcel may well exist."""
    transport = _transport()
    transport.enqueue(
        "POST", str(Endpoint.CREATE_ORDER), status_code=201, body=json.dumps({"data": {}})
    )
    result = await _adapter(transport).create_consignment(_creds(), _booking(), "ECB-1001")

    assert result.outcome is BookingOutcome.UNKNOWN


@pytest.mark.asyncio
async def test_a_bulk_batch_reports_every_sent_item_as_unresolved() -> None:
    """Pathao publishes no per-item schema, so a success cannot be evidenced."""
    transport = _transport()
    transport.enqueue(
        "POST", str(Endpoint.CREATE_ORDER_BULK), status_code=200, body=json.dumps({"data": []})
    )
    results = await _adapter(transport).create_bulk(
        _creds(), [_booking(merchant_reference="ECB-1"), _booking(merchant_reference="ECB-2")]
    )

    assert [r.outcome for r in results] == [BookingOutcome.UNKNOWN, BookingOutcome.UNKNOWN]
    assert len(transport.calls_to("POST", str(Endpoint.CREATE_ORDER_BULK))) == 1


@pytest.mark.asyncio
async def test_a_locally_invalid_item_fails_safely_inside_a_batch() -> None:
    transport = _transport()
    transport.enqueue(
        "POST", str(Endpoint.CREATE_ORDER_BULK), status_code=200, body=json.dumps({"data": []})
    )
    results = await _adapter(transport).create_bulk(
        _creds(),
        [
            _booking(merchant_reference="ECB-1"),
            _booking(merchant_reference="ECB-2", recipient_address="Dhaka"),
        ],
    )

    assert results[0].outcome is BookingOutcome.UNKNOWN
    # Never sent, so it cannot be ambiguous.
    assert results[1].outcome is BookingOutcome.FAILED


# ------------------------------------------------------------ credentials --


@pytest.mark.asyncio
async def test_rejected_credentials_are_reported_as_rejected() -> None:
    transport = FakeProviderTransport(provider="pathao")
    transport.always(
        "POST", str(Endpoint.LOGIN), status_code=401, body=json.dumps({"message": "bad client"})
    )
    result = await _adapter(transport).validate_credentials(_creds())

    assert not result.valid
    assert result.rejected


@pytest.mark.asyncio
async def test_a_provider_outage_never_marks_credentials_wrong() -> None:
    """Telling a seller their working key is wrong is how trust is lost."""
    transport = _transport()
    transport.always(
        "GET", str(Endpoint.USER_SHORT_INFO), status_code=503, body=json.dumps({"m": "down"})
    )
    result = await _adapter(transport).validate_credentials(_creds())

    assert not result.valid
    assert not result.rejected
    assert result.is_inconclusive


@pytest.mark.asyncio
async def test_valid_credentials_report_the_published_capabilities() -> None:
    transport = _transport()
    transport.always("GET", str(Endpoint.USER_SHORT_INFO), body=json.dumps({"data": {}}))
    result = await _adapter(transport).validate_credentials(_creds())

    assert result.valid
    assert Capability.CREATE_SINGLE in result.detected_capabilities
    assert Capability.STATUS_LOOKUP not in result.detected_capabilities


def test_credentials_never_appear_in_their_own_repr() -> None:
    creds = _creds()
    assert CLIENT_ID not in repr(creds)
    assert CLIENT_SECRET not in repr(creds)
    assert CLIENT_SECRET not in str(creds)
    assert creds.masked_identifier.endswith("0001")


# ---------------------------------------------------------- capabilities --


@pytest.mark.asyncio
async def test_status_lookup_reports_unavailable_rather_than_guessing_a_path() -> None:
    """A community SDK's endpoint is not a contract."""
    result = await _adapter(_transport()).get_status(_creds(), "DA200101")

    assert isinstance(result, Unavailable)
    assert result.capability is Capability.STATUS_LOOKUP
    assert "webhook" in result.reason.lower()


@pytest.mark.asyncio
async def test_unsupported_capabilities_return_a_reason_not_an_exception() -> None:
    adapter = _adapter(_transport())
    for call in (
        adapter.cancel(_creds(), "DA1"),
        adapter.request_return(_creds(), "DA1", "damaged"),
        adapter.get_balance(_creds()),
        adapter.customer_stats(_creds(), "+8801712345678"),
    ):
        result = await call
        assert isinstance(result, Unavailable)
        assert result.reason


@pytest.mark.asyncio
async def test_stores_are_listed_from_the_published_field_names() -> None:
    transport = _transport()
    transport.always(
        "GET",
        str(Endpoint.STORES),
        body=json.dumps(
            {"data": {"data": [{"store_id": 12345, "store_name": "Mirpur Warehouse"}]}}
        ),
    )
    stores = await _adapter(transport).list_stores(_creds())

    assert len(stores) == 1
    assert stores[0].provider_store_id == "12345"
    assert stores[0].name == "Mirpur Warehouse"


# -------------------------------------------------------------- webhooks --


def test_a_correctly_signed_callback_verifies() -> None:
    verifier = PathaoWebhookVerifier("shop-secret-xyz")
    assert verifier.is_configured
    assert verifier.verify(headers={"x-pathao-signature": "shop-secret-xyz"}, body=b"{}")


def test_a_wrong_or_missing_signature_is_refused() -> None:
    verifier = PathaoWebhookVerifier("shop-secret-xyz")
    assert not verifier.verify(headers={"x-pathao-signature": "someone-elses"}, body=b"{}")
    assert not verifier.verify(headers={}, body=b"{}")


def test_a_shop_with_no_secret_is_not_configured_rather_than_open() -> None:
    verifier = PathaoWebhookVerifier(None)
    assert not verifier.is_configured
    assert not verifier.verify(headers={"x-pathao-signature": "anything"}, body=b"{}")


def test_the_integration_handshake_is_recognised() -> None:
    assert is_integration_handshake(json.dumps({"event": "webhook_integration"}).encode())
    assert not is_integration_handshake(json.dumps({"event": "order.delivered"}).encode())


def test_a_callback_is_parsed_into_a_normalized_event() -> None:
    body = json.dumps(
        {
            "event": "order.delivered",
            "consignment_id": "DA200101",
            "merchant_order_id": "ECB-1001",
            "order_status": "Delivered",
            "delivery_fee": 70,
        }
    ).encode()
    events = parse_pathao_events(body)

    assert len(events) == 1
    assert events[0].provider_consignment_id == "DA200101"
    assert events[0].merchant_reference == "ECB-1001"
    assert events[0].raw_status == "Delivered"
    assert events[0].delivery_fee == 70
    # Pathao publishes neither, so neither is invented.
    assert events[0].provider_event_id is None
    assert events[0].occurred_at is None


def test_an_unreadable_callback_is_data_not_a_crash() -> None:
    assert parse_pathao_events(b"not json at all") == []
    assert parse_pathao_events(b"") == []


def test_a_callback_naming_no_parcel_produces_no_event() -> None:
    body = json.dumps({"event": "order.delivered", "order_status": "Delivered"}).encode()
    assert parse_pathao_events(body) == []


# --------------------------------------------------------------- manifest --


def test_the_manifest_matches_what_the_adapter_claims() -> None:
    """The manifest is the reviewed artefact; the code must not exceed it."""
    manifest = load_manifest("pathao")
    assert manifest is not None
    assert manifest.supports(Capability.CREATE_SINGLE)
    assert manifest.supports(Capability.WEBHOOK)
    # Absent from Pathao's published integration, so absent here.
    assert not manifest.supports(Capability.STATUS_LOOKUP)
    assert not manifest.supports(Capability.PRICE_QUOTE)
    assert not manifest.supports(Capability.CANCEL)
    assert "PATHAO_STATUS_LOOKUP_CONTRACT_REQUIRED" in manifest.blockers
