"""RedX, implemented against RedX's own developer documentation.

Every test here defends a claim that costs a seller money or trust if it breaks:

* the token is sent exactly as the documentation shows, only to the documented
  hosts, and never comes back out of the API;
* a credential check reads, never creates, and a RedX outage never marks a
  working token wrong;
* a create is sent once; a lost answer is ``BOOKING_UNKNOWN`` and the order
  cannot be booked again from the normal path;
* a booking with no delivery area is refused before anything exists;
* a RedX status settles a parcel only when its delivery type says which outcome
  it is;
* a callback is accepted only with this shop's token in its URL, and that
  token never reaches a log line.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient

from app.common.money import BDT, Money
from app.consignments.models import Consignment, ConsignmentStatus
from app.consignments.service import ConsignmentService
from app.core.context import RequestContext, set_context
from app.core.logging import JsonFormatter
from app.core.redaction import redact_text
from app.core.security import CredentialVault
from app.couriers.accounts import ConnectRequest, CourierAccountService
from app.couriers.adapter import BookingOutcome, BookingRequest, Unavailable
from app.couriers.capabilities import Capability, load_manifest
from app.couriers.credentials import build_credentials, spec_for
from app.couriers.http import (
    FakeProviderTransport,
    ProviderAmbiguousError,
    ProviderUnavailableError,
)
from app.couriers.models import CourierAccount, CourierAccountStatus, CredentialValidation
from app.couriers.redx.adapter import SUPPORTED_CAPABILITIES, RedxAdapter
from app.couriers.redx.client import RedxClient, RedxConfig, RedxCredentials
from app.couriers.redx.contract import (
    HEADER_AUTH,
    LIVE_BASE_URL,
    SANDBOX_BASE_URL,
    DeliveryType,
    RedxStatus,
)
from app.couriers.redx.mapping import assert_map_is_complete, map_parcel_status
from app.couriers.redx.webhooks import RedxWebhookVerifier, parse_redx_events
from app.couriers.registry import (
    CourierAdapterRegistry,
    build_registry,
    set_courier_registry,
)
from app.couriers.status_maps import map_courier_status
from app.couriers.status_sync import StatusSyncService
from app.money.service import ReceivableService
from app.tenants.roles import TenantRole
from tests.conftest_commerce import create_order, create_product, signed_in_shop
from tests.test_auth_flow import auth_header, sign_in
from tests.test_courier_security import _demote

# Each test here is about a courier a shop may use, so the kill switches are on.
pytestmark = pytest.mark.usefixtures("courier_flags_on")

# Synthetic, and deliberately not shaped like a real RedX token.
TOKEN = "synthetic-redx-token-for-tests-9f3a"

#: The version prefix the fake transport sees once the host is stripped.
P = "/v1.0.0-beta"

STORES_OK = json.dumps(
    {
        "pickup_stores": [
            {
                "id": 7,
                "name": "Mirpur Store",
                "address": "Road 1, Mirpur",
                "area_name": "Mirpur",
                "area_id": 12,
                "phone": "8801898000999",
                "created_at": "2021-09-13T10:39:15.000Z",
            }
        ]
    }
)
STORE_INFO = json.dumps(
    {
        "pickup_store": {
            "id": 7,
            "name": "Mirpur Store",
            "address": "Road 1, Mirpur",
            "area_name": "Mirpur",
            "area_id": 12,
            "phone": "8801898000999",
            "created_at": "2021-09-13T10:39:15.000Z",
        }
    }
)
AREAS_OK = json.dumps(
    {
        "areas": [
            {
                "id": 1,
                "name": "Mohammadpur(Dhaka)",
                "post_code": 1207,
                "division_name": "Dhaka",
                "zone_id": 1,
            },
            {
                "id": 2,
                "name": "Dhanmondi",
                "post_code": 1209,
                "division_name": "Dhaka",
                "zone_id": 1,
            },
        ]
    }
)
TRACKING_ID = "20A312THJDJ8"
CREATE_OK = json.dumps({"tracking_id": TRACKING_ID})
CHARGE_OK = json.dumps({"deliveryCharge": 60, "codCharge": 10.5})
UPDATE_OK = json.dumps({"success": True, "message": "Request Accepted"})


def _info(status: str, delivery_type: str = "regular") -> str:
    return json.dumps(
        {
            "parcel": {
                "tracking_id": TRACKING_ID,
                "customer_address": "House 12",
                "delivery_area": "Mirpur DOHS",
                "delivery_area_id": 12,
                "charge": 60,
                "customer_name": "Test",
                "customer_phone": "01987654321",
                "cash_collection_amount": 1250,
                "parcel_weight": 500,
                "merchant_invoice_id": "CP-1",
                "status": status,
                "instruction": "",
                "created_at": "2021-04-27T08:29:14.000Z",
                "delivery_type": delivery_type,
                "value": "0",
                "pickup_location": {
                    "id": 1,
                    "name": "Malibag",
                    "address": "Malibagh",
                    "area_name": "Malibag",
                    "area_id": 1,
                },
            }
        }
    )


async def _no_sleep(_seconds: float) -> None:
    return None


def _adapter(transport: FakeProviderTransport) -> RedxAdapter:
    return RedxAdapter(
        RedxClient(transport, config=RedxConfig(max_read_retries=0), sleep=_no_sleep)
    )


def _creds(*, sandbox: bool = False) -> RedxCredentials:
    return RedxCredentials(api_token=TOKEN, sandbox=sandbox)


def _booking(**overrides: Any) -> BookingRequest:
    base: dict[str, Any] = {
        "order_id": uuid.uuid4(),
        "merchant_reference": "CP-20260923-0001",
        "recipient_name": "Rahim Uddin",
        "recipient_phone_e164": "+8801712345678",
        "recipient_address": "House 12, Road 5, Mirpur DOHS, Dhaka",
        "cod_amount": Money(paisa=125_050, currency=BDT),
        "item_description": "Cotton shirt",
        "delivery_area_id": "12",
        "delivery_area_name": "Mirpur DOHS",
    }
    base.update(overrides)
    return BookingRequest(**base)


# ================================================================ contract ==


def test_the_documented_hosts_and_header_are_used() -> None:
    assert LIVE_BASE_URL == "https://openapi.redx.com.bd/v1.0.0-beta"
    assert SANDBOX_BASE_URL == "https://sandbox.redx.com.bd/v1.0.0-beta"
    assert HEADER_AUTH == "API-ACCESS-TOKEN"


def test_the_manifest_claims_exactly_what_the_adapter_implements() -> None:
    manifest = load_manifest("redx")
    assert manifest is not None

    claimed = {capability for capability in Capability if manifest.supports(capability)}
    assert claimed == set(SUPPORTED_CAPABILITIES)
    assert manifest.verified_at is not None
    assert "redx.com.bd/developer-api" in (manifest.documentation_source or "")
    # Documented as absent, not left open.
    for absent in (
        Capability.CREATE_BULK,
        Capability.RETURNS,
        Capability.BALANCE,
        Capability.PAYOUTS,
        Capability.CUSTOMER_STATS,
        Capability.AUTO_ADDRESS,
    ):
        assert str(manifest.state(absent)) == "false", absent


def test_the_adapter_is_registered_with_a_real_transport() -> None:
    adapter = build_registry().get("redx")
    assert isinstance(adapter, RedxAdapter)
    assert adapter.bulk_chunk_size == 1


# ============================================================ credentials ==


def test_the_connect_form_asks_for_the_one_documented_credential() -> None:
    spec = spec_for("redx")
    assert spec is not None

    assert [field.name for field in spec.fields] == ["api_token"]
    field = spec.fields[0]
    assert field.secret is True
    assert field.input_type == "password"
    assert field.label_en and field.label_bn
    assert field.help_en and field.help_bn
    assert spec.is_single_secret
    form = spec.as_dict()
    assert form["supports_sandbox"] is True
    assert form["requires_delivery_area"] is True
    assert form["webhook_secret_generated"] is True
    assert form["requires_store"] is False


def test_the_second_storage_slot_is_never_read_for_redx() -> None:
    creds = build_credentials("redx", primary=TOKEN, secondary="", config={"sandbox": True})

    assert isinstance(creds, RedxCredentials)
    assert creds.api_token == TOKEN
    assert creds.sandbox is True


def test_the_token_never_appears_in_its_own_repr() -> None:
    creds = _creds()

    assert TOKEN not in repr(creds)
    assert TOKEN not in str(creds)
    assert creds.masked_identifier == "****9f3a"


async def test_the_token_is_sent_exactly_as_the_documentation_shows() -> None:
    transport = FakeProviderTransport(provider="redx")
    transport.enqueue("GET", f"{P}/pickup/stores", body=STORES_OK)

    result = await _adapter(transport).validate_credentials(_creds())

    assert result.valid
    call = transport.calls[0]
    assert call["url"] == f"{LIVE_BASE_URL}/pickup/stores"
    assert call["headers"][HEADER_AUTH] == f"Bearer {TOKEN}"
    assert call["idempotent"] is True


async def test_a_credential_check_never_creates_anything() -> None:
    transport = FakeProviderTransport(provider="redx")
    transport.enqueue("GET", f"{P}/pickup/stores", body=STORES_OK)

    await _adapter(transport).validate_credentials(_creds())

    assert {call["method"] for call in transport.calls} == {"GET"}
    assert transport.calls_to("POST", f"{P}/parcel") == []


async def test_sandbox_credentials_go_to_the_sandbox_host() -> None:
    transport = FakeProviderTransport(provider="redx")
    transport.enqueue("GET", f"{P}/pickup/stores", body=STORES_OK)

    await _adapter(transport).validate_credentials(_creds(sandbox=True))

    assert transport.calls[0]["url"].startswith(SANDBOX_BASE_URL)


async def test_a_refused_token_is_reported_as_rejected() -> None:
    transport = FakeProviderTransport(provider="redx")
    transport.enqueue(
        "GET", f"{P}/pickup/stores", status_code=401, body='{"message":"invalid token"}'
    )

    result = await _adapter(transport).validate_credentials(_creds())

    assert not result.valid
    assert result.rejected
    assert TOKEN not in (result.message or "")


@pytest.mark.parametrize(
    "script",
    [
        {"status_code": 500, "body": "{}"},
        {
            "status_code": 200,
            "body": "<html>proxy</html>",
            "headers": {"content-type": "text/html"},
        },
        {"status_code": 200, "body": '{"unexpected": true}'},
    ],
    ids=["5xx", "html", "unreadable"],
)
async def test_a_redx_problem_never_marks_the_token_wrong(script: dict[str, Any]) -> None:
    transport = FakeProviderTransport(provider="redx")
    transport.enqueue("GET", f"{P}/pickup/stores", **script)

    result = await _adapter(transport).validate_credentials(_creds())

    assert result.is_inconclusive
    assert not result.rejected


async def test_a_connection_failure_never_marks_the_token_wrong() -> None:
    transport = FakeProviderTransport(provider="redx")
    transport.enqueue_error(
        "GET",
        f"{P}/pickup/stores",
        ProviderUnavailableError("down", provider="redx", reached_provider=False),
    )

    result = await _adapter(transport).validate_credentials(_creds())

    assert result.is_inconclusive


async def test_capabilities_do_not_call_redx() -> None:
    transport = FakeProviderTransport(provider="redx")

    assert await _adapter(transport).capabilities(_creds()) == set(SUPPORTED_CAPABILITIES)
    assert transport.calls == []


# ================================================================= booking ==


async def test_a_successful_create_returns_the_tracking_id() -> None:
    transport = FakeProviderTransport(provider="redx")
    transport.enqueue("POST", f"{P}/parcel", body=CREATE_OK)

    result = await _adapter(transport).create_consignment(
        _creds(), _booking(store_reference="7"), "CP-20260923-0001"
    )

    assert result.outcome is BookingOutcome.BOOKED
    assert result.consignment is not None
    assert result.consignment.provider_consignment_id == TRACKING_ID
    assert result.consignment.tracking_code == TRACKING_ID

    sent = transport.calls_to("POST", f"{P}/parcel")
    assert len(sent) == 1
    body = sent[0]["json"]
    assert body == {
        "customer_name": "Rahim Uddin",
        "customer_phone": "01712345678",
        "delivery_area": "Mirpur DOHS",
        "delivery_area_id": 12,
        "customer_address": "House 12, Road 5, Mirpur DOHS, Dhaka",
        "merchant_invoice_id": "CP-20260923-0001",
        # Whole taka, as the string the documentation types it as.
        "cash_collection_amount": "1251",
        "parcel_weight": 500,
        "value": 1251,
        "pickup_store_id": 7,
    }
    assert sent[0]["idempotent"] is False
    assert sent[0]["headers"]["Content-Type"] == "application/json"


async def test_the_declared_value_is_the_goods_value_when_known() -> None:
    transport = FakeProviderTransport(provider="redx")
    transport.enqueue("POST", f"{P}/parcel", body=CREATE_OK)

    await _adapter(transport).create_consignment(
        _creds(),
        _booking(declared_value=Money(paisa=200_000), weight_grams=1200, note="Fragile"),
        "CP-1",
    )

    body = transport.calls_to("POST", f"{P}/parcel")[0]["json"]
    assert body["value"] == 2000
    assert body["parcel_weight"] == 1200
    assert body["instruction"] == "Fragile"
    assert "pickup_store_id" not in body


async def test_a_booking_with_no_delivery_area_is_refused_before_anything_is_sent() -> None:
    transport = FakeProviderTransport(provider="redx")

    result = await _adapter(transport).create_consignment(
        _creds(), _booking(delivery_area_id=None, delivery_area_name=None), "CP-1"
    )

    assert result.outcome is BookingOutcome.FAILED
    assert transport.calls == []


async def test_a_create_is_sent_once_even_when_redx_fails_hard() -> None:
    transport = FakeProviderTransport(provider="redx")
    transport.always("POST", f"{P}/parcel", status_code=502, body="{}")

    result = await _adapter(transport).create_consignment(_creds(), _booking(), "CP-1")

    # RedX answered with a 5xx, so it received the request: the parcel may
    # exist. Ambiguous, and never re-sent.
    assert result.outcome is BookingOutcome.UNKNOWN
    assert len(transport.calls_to("POST", f"{P}/parcel")) == 1


async def test_a_lost_answer_is_unknown_not_failed() -> None:
    transport = FakeProviderTransport(provider="redx")
    transport.enqueue_error(
        "POST", f"{P}/parcel", ProviderAmbiguousError("read timeout", provider="redx")
    )

    result = await _adapter(transport).create_consignment(_creds(), _booking(), "CP-1")

    assert result.outcome is BookingOutcome.UNKNOWN


async def test_a_request_that_never_left_is_a_clean_failure() -> None:
    transport = FakeProviderTransport(provider="redx")
    transport.enqueue_error(
        "POST",
        f"{P}/parcel",
        ProviderUnavailableError("connect", provider="redx", reached_provider=False),
    )

    result = await _adapter(transport).create_consignment(_creds(), _booking(), "CP-1")

    assert result.outcome is BookingOutcome.FAILED


async def test_a_refused_create_is_a_clean_failure() -> None:
    transport = FakeProviderTransport(provider="redx")
    transport.enqueue("POST", f"{P}/parcel", status_code=400, body='{"message":"bad area"}')

    result = await _adapter(transport).create_consignment(_creds(), _booking(), "CP-1")

    assert result.outcome is BookingOutcome.FAILED
    # The seller reads ecomsbd's words, not RedX's raw body.
    assert "bad area" not in (result.error_message or "")


@pytest.mark.parametrize(
    "script",
    [
        {"status_code": 200, "body": '{"message": "ok"}'},
        {
            "status_code": 201,
            "body": "<html>gateway</html>",
            "headers": {"content-type": "text/html"},
        },
        {"status_code": 200, "body": "not json"},
    ],
    ids=["no-tracking-id", "html", "not-json"],
)
async def test_an_unreadable_success_is_ambiguous(script: dict[str, Any]) -> None:
    transport = FakeProviderTransport(provider="redx")
    transport.enqueue("POST", f"{P}/parcel", **script)

    result = await _adapter(transport).create_consignment(_creds(), _booking(), "CP-1")

    assert result.outcome is BookingOutcome.UNKNOWN


async def test_bulk_is_refused_per_item_and_nothing_is_sent() -> None:
    """Clean failures, not Unavailable: the booking service would read a
    non-list answer as "the batch's outcome is unknown"."""
    transport = FakeProviderTransport(provider="redx")

    results = await _adapter(transport).create_bulk(_creds(), [_booking(), _booking()])

    assert isinstance(results, list)
    assert [result.outcome for result in results] == [BookingOutcome.FAILED] * 2
    assert transport.calls == []


def test_an_invalid_phone_or_reference_is_refused_locally() -> None:
    adapter = _adapter(FakeProviderTransport(provider="redx"))

    with pytest.raises(ValueError):
        adapter.describe_booking(_booking(recipient_phone_e164="+12025550123"), "CP-1")
    with pytest.raises(ValueError):
        adapter.describe_booking(_booking(), "CP 1/bad")


# ================================================================== status ==


def test_every_published_status_is_mapped() -> None:
    assert_map_is_complete()


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (RedxStatus.READY_FOR_DELIVERY, ConsignmentStatus.PICKED_UP),
        (RedxStatus.DELIVERY_IN_PROGRESS, ConsignmentStatus.OUT_FOR_DELIVERY),
        (RedxStatus.AGENT_HOLD, ConsignmentStatus.IN_TRANSIT),
        (RedxStatus.AGENT_RETURNING, ConsignmentStatus.RETURNING),
    ],
)
def test_in_flight_statuses_move_the_parcel_but_never_money(
    status: RedxStatus, expected: ConsignmentStatus
) -> None:
    mapping = map_parcel_status(str(status), "regular")

    assert mapping.canonical is expected
    assert not mapping.is_final
    assert not mapping.moves_money


def test_delivered_on_a_regular_parcel_settles_as_delivered() -> None:
    mapping = map_parcel_status("delivered", "regular")

    assert mapping.canonical is ConsignmentStatus.DELIVERED
    assert mapping.is_final and mapping.moves_money


def test_delivered_on_a_partial_parcel_waits_for_quantities() -> None:
    mapping = map_parcel_status("delivered", "partial-delivery")

    assert mapping.canonical is ConsignmentStatus.PARTIAL_DELIVERED
    assert mapping.needs_quantity_resolution


@pytest.mark.parametrize(
    "delivery_type",
    [None, "exchange-delivery", "exchange-return", "reverse", "partial-return", "something-new"],
)
def test_a_final_status_without_a_regular_delivery_type_settles_nothing(
    delivery_type: str | None,
) -> None:
    for status in ("delivered", "returned", "cancelled"):
        mapping = map_parcel_status(status, delivery_type)
        if status == "delivered" and delivery_type == DeliveryType.PARTIAL_DELIVERY:
            continue
        assert mapping.canonical is None, (status, delivery_type)
        assert not mapping.is_final
        assert mapping.is_documented


def test_returned_and_cancelled_settle_on_a_regular_parcel() -> None:
    assert map_parcel_status("returned", "regular").canonical is ConsignmentStatus.RETURNED
    assert map_parcel_status("cancelled", "regular").canonical is ConsignmentStatus.CANCELLED


@pytest.mark.parametrize("status", ["pickup-pending", "agent-area-change", "paid"])
def test_documented_statuses_with_no_location_claim_change_nothing(status: str) -> None:
    mapping = map_parcel_status(status, "regular")

    assert mapping.canonical is None
    assert mapping.is_documented
    assert not mapping.is_final


def test_an_unknown_status_is_data_not_a_guess() -> None:
    mapping = map_parcel_status("delivered-ish", "regular")

    assert mapping.canonical is None
    assert not mapping.is_documented
    assert mapping.keep_polling


def test_redx_statuses_are_not_read_against_steadfasts_table() -> None:
    """Steadfast's `delivered` settles on its own; RedX's needs its delivery type."""
    assert map_courier_status("steadfast", "delivered").canonical is ConsignmentStatus.DELIVERED
    assert map_courier_status("redx", "delivered").canonical is None
    assert (
        map_courier_status("redx", "delivered", detail="regular").canonical
        is ConsignmentStatus.DELIVERED
    )
    # A RedX word Steadfast has never used.
    assert map_courier_status("redx", "returned", detail="regular").canonical is (
        ConsignmentStatus.RETURNED
    )


async def test_a_status_lookup_reads_parcel_info() -> None:
    transport = FakeProviderTransport(provider="redx")
    transport.enqueue("GET", f"{P}/parcel/info/{TRACKING_ID}", body=_info("delivery-in-progress"))

    status = await _adapter(transport).get_status(_creds(), TRACKING_ID)

    assert not isinstance(status, Unavailable)
    assert status.raw_status == "delivery-in-progress"
    assert status.normalized_status == str(ConsignmentStatus.OUT_FOR_DELIVERY)
    assert status.raw["delivery_type"] == "regular"


async def test_a_parcel_without_a_status_is_a_protocol_error_not_a_state() -> None:
    from app.couriers.http import ProviderProtocolError

    transport = FakeProviderTransport(provider="redx")
    transport.enqueue(
        "GET", f"{P}/parcel/info/{TRACKING_ID}", body=json.dumps({"parcel": {"tracking_id": "X"}})
    )

    with pytest.raises(ProviderProtocolError):
        await _adapter(transport).get_status(_creds(), TRACKING_ID)


# ======================================================= reference data ==


async def test_areas_are_read_with_the_documented_filter() -> None:
    transport = FakeProviderTransport(provider="redx")
    transport.enqueue("GET", f"{P}/areas", body=AREAS_OK)

    areas = await _adapter(transport).list_delivery_areas(_creds(), district_name="Dhaka")

    assert [(area.id, area.name) for area in areas] == [
        (1, "Mohammadpur(Dhaka)"),
        (2, "Dhanmondi"),
    ]
    assert transport.calls[0]["url"] == f"{LIVE_BASE_URL}/areas?district_name=Dhaka"


async def test_stores_are_listed_from_the_documented_field_names() -> None:
    transport = FakeProviderTransport(provider="redx")
    transport.enqueue("GET", f"{P}/pickup/stores", body=STORES_OK)

    stores = await _adapter(transport).list_stores(_creds())

    assert isinstance(stores, list)
    assert [(store.provider_store_id, store.name) for store in stores] == [("7", "Mirpur Store")]


async def test_a_quote_needs_a_store_and_an_area_and_says_so() -> None:
    transport = FakeProviderTransport(provider="redx")

    result = await _adapter(transport).quote(_creds(), _booking(store_reference=None))

    assert isinstance(result, Unavailable)
    assert transport.calls == []


async def test_a_quote_prices_from_the_pickup_stores_area() -> None:
    transport = FakeProviderTransport(provider="redx")
    transport.enqueue("GET", f"{P}/pickup/store/info/7", body=STORE_INFO)
    transport.enqueue("GET", f"{P}/charge/charge_calculator", body=CHARGE_OK)

    result = await _adapter(transport).quote(_creds(), _booking(store_reference="7"))

    assert not isinstance(result, Unavailable)
    assert result.delivery_fee.paisa == 6000
    assert result.cod_fee is not None and result.cod_fee.paisa == 1050
    charge_url = transport.calls[1]["url"]
    assert "delivery_area_id=12" in charge_url
    assert "pickup_area_id=12" in charge_url
    assert "cash_collection_amount=1251" in charge_url
    assert "weight=500" in charge_url
    assert transport.calls_to("POST", f"{P}/parcel") == []


async def test_cancel_uses_the_documented_update() -> None:
    transport = FakeProviderTransport(provider="redx")
    transport.enqueue("PATCH", f"{P}/parcels", body=UPDATE_OK)

    assert await _adapter(transport).cancel(_creds(), TRACKING_ID) is True
    call = transport.calls[0]
    assert call["json"] == {
        "entity_type": "parcel-tracking-id",
        "entity_id": TRACKING_ID,
        "update_details": {"property_name": "status", "new_value": "cancelled"},
    }
    assert call["idempotent"] is False


async def test_unsupported_capabilities_return_a_reason_not_an_exception() -> None:
    adapter = _adapter(FakeProviderTransport(provider="redx"))

    for result in (
        await adapter.request_return(_creds(), TRACKING_ID, "x"),
        await adapter.get_balance(_creds()),
        await adapter.customer_stats(_creds(), "+8801712345678"),
    ):
        assert isinstance(result, Unavailable)
        assert result.reason


# ================================================================ webhooks ==

CALLBACK = json.dumps(
    {
        "tracking_number": TRACKING_ID,
        "timestamp": "2026-09-23T10:00:00.000Z",
        "status": "delivery-in-progress",
        "message_en": "Parcel dispatched",
        "message_bn": "পার্সেল রওনা হয়েছে",
        "invoice_number": "CP-1",
        "delivery_type": "regular",
    }
).encode()


def test_a_callback_with_this_shops_token_verifies() -> None:
    assert RedxWebhookVerifier("shop-secret", provided_token="shop-secret").verify(
        headers={}, body=CALLBACK
    )


def test_a_wrong_or_missing_token_is_refused() -> None:
    assert not RedxWebhookVerifier("shop-secret", provided_token="other").verify(
        headers={}, body=CALLBACK
    )
    assert not RedxWebhookVerifier("shop-secret", provided_token=None).verify(
        headers={}, body=CALLBACK
    )


def test_a_shop_with_no_secret_is_not_configured_rather_than_open() -> None:
    verifier = RedxWebhookVerifier(None, provided_token="anything")

    assert not verifier.is_configured
    assert not verifier.verify(headers={}, body=CALLBACK)


def test_a_callback_is_parsed_into_a_normalized_event() -> None:
    [event] = parse_redx_events(CALLBACK)

    assert event.provider_consignment_id == TRACKING_ID
    assert event.merchant_reference == "CP-1"
    assert event.raw_status == "delivery-in-progress"
    assert event.delivery_type == "regular"
    assert event.occurred_at is not None
    # RedX sends no event id; inventing one would claim something it did not say.
    assert event.provider_event_id is None


def test_an_unreadable_callback_is_data_not_a_crash() -> None:
    assert parse_redx_events(b"not json") == []
    assert parse_redx_events(b'{"status": "delivered"}') == []


def test_a_query_string_token_never_reaches_a_log_line() -> None:
    line = f'1.2.3.4:0 - "POST /v1/webhooks/couriers/redx/route?token={TOKEN} HTTP/1.1" 200'

    scrubbed = redact_text(line)

    assert TOKEN not in scrubbed
    assert "token=[redacted]" in scrubbed

    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "%s", (line,), None)
    assert TOKEN not in JsonFormatter().format(record)


# ============================================== service and API, with DB ==


@pytest.fixture
def redx_transport() -> FakeProviderTransport:
    """A scripted RedX transport, installed into the adapter registry."""
    transport = FakeProviderTransport(provider="redx")

    def _factory() -> RedxAdapter:
        return _adapter(transport)

    set_courier_registry(CourierAdapterRegistry({"redx": _factory}))
    return transport


async def _connect(client: AsyncClient, shop: dict[str, Any], **config: Any) -> Any:
    return await client.post(
        "/v1/couriers/accounts/redx/connect",
        headers=auth_header(shop),
        json={"credentials": {"api_token": TOKEN}, "config": config},
    )


async def _order(client: AsyncClient, shop: dict[str, Any]) -> dict[str, Any]:
    product = await create_product(client, shop, name="RedX Item", sku=f"RX-{uuid.uuid4().hex[:6]}")
    created = await create_order(
        client,
        shop,
        items=[{"product_id": product["id"], "quantity": 1, "unit_price_paisa": 125_000}],
        cod_amount_paisa=125_000,
        address="House 12, Road 3, Mirpur DOHS, Dhaka",
    )
    return created["order"]


async def test_connecting_stores_only_ciphertext_and_returns_a_masked_hint(
    client: AsyncClient, db, unique_phone: str, redx_transport: FakeProviderTransport
) -> None:
    shop = await signed_in_shop(client, unique_phone, shop_name="RedX Shop")
    redx_transport.enqueue("GET", f"{P}/pickup/stores", body=STORES_OK)

    response = await _connect(client, shop)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["result"] == str(CredentialValidation.VALID)
    assert body["account"]["masked_identifier"] == "****9f3a"
    assert TOKEN not in response.text

    listed = await client.get("/v1/couriers/accounts", headers=auth_header(shop))
    assert TOKEN not in listed.text
    [row] = [row for row in listed.json() if row["provider"] == "redx"]
    assert row["connected"] is True

    set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(shop["tenant_id"])))
    account = (
        await db.execute(sa.select(CourierAccount).where(CourierAccount.provider == "redx"))
    ).scalar_one()
    assert account.api_key_encrypted and TOKEN not in account.api_key_encrypted
    # The unused second slot is an encrypted empty value, never the token.
    assert account.secret_key_encrypted and TOKEN not in account.secret_key_encrypted
    assert redx_transport.calls_to("POST", f"{P}/parcel") == []


async def test_a_rejected_token_is_not_stored(
    client: AsyncClient, unique_phone: str, redx_transport: FakeProviderTransport
) -> None:
    shop = await signed_in_shop(client, unique_phone, shop_name="RedX Reject")
    redx_transport.enqueue(
        "GET", f"{P}/pickup/stores", status_code=401, body='{"message":"invalid token"}'
    )

    response = await _connect(client, shop)

    assert response.status_code == 400, response.text
    assert response.json()["code"] == "INVALID_COURIER_CREDENTIALS"
    assert TOKEN not in response.text
    listed = await client.get("/v1/couriers/accounts", headers=auth_header(shop))
    assert all(row["provider"] != "redx" for row in listed.json())


async def test_a_missing_token_is_refused_before_redx_is_asked(
    client: AsyncClient, unique_phone: str, redx_transport: FakeProviderTransport
) -> None:
    shop = await signed_in_shop(client, unique_phone, shop_name="RedX Empty")

    response = await client.post(
        "/v1/couriers/accounts/redx/connect",
        headers=auth_header(shop),
        json={"credentials": {"api_token": "  "}},
    )

    assert response.status_code == 422
    assert redx_transport.calls == []


async def test_test_connection_and_disconnect(
    client: AsyncClient, unique_phone: str, redx_transport: FakeProviderTransport
) -> None:
    shop = await signed_in_shop(client, unique_phone, shop_name="RedX Manage")
    redx_transport.always("GET", f"{P}/pickup/stores", body=STORES_OK)
    assert (await _connect(client, shop, sandbox=True)).status_code == 201

    tested = await client.post("/v1/couriers/accounts/redx/test", headers=auth_header(shop))
    assert tested.status_code == 200
    assert tested.json()["result"] == str(CredentialValidation.VALID)
    assert tested.json()["account"]["config"] == {"sandbox": True}
    assert all(call["url"].startswith(SANDBOX_BASE_URL) for call in redx_transport.calls)

    gone = await client.delete("/v1/couriers/accounts/redx", headers=auth_header(shop))
    assert gone.status_code == 200
    assert gone.json()["status"] == str(CourierAccountStatus.DISCONNECTED)
    again = await client.post("/v1/couriers/accounts/redx/test", headers=auth_header(shop))
    assert again.status_code == 404


async def test_a_packer_cannot_connect_redx(
    client: AsyncClient, unique_phone: str, redx_transport: FakeProviderTransport
) -> None:
    shop = await signed_in_shop(client, unique_phone, shop_name="RedX Packer")
    await _demote(shop, TenantRole.PACKER)
    refreshed = await sign_in(client, unique_phone)

    response = await _connect(client, refreshed)

    assert response.status_code == 403
    assert redx_transport.calls == []


async def test_one_shop_cannot_see_another_shops_redx_account(
    client: AsyncClient, redx_transport: FakeProviderTransport
) -> None:
    first = await signed_in_shop(client, "01711700001", shop_name="RedX One")
    second = await signed_in_shop(client, "01711700002", shop_name="RedX Two")
    redx_transport.always("GET", f"{P}/pickup/stores", body=STORES_OK)
    assert (await _connect(client, first)).status_code == 201

    listed = await client.get("/v1/couriers/accounts", headers=auth_header(second))
    assert all(row["provider"] != "redx" for row in listed.json())
    tested = await client.post("/v1/couriers/accounts/redx/test", headers=auth_header(second))
    assert tested.status_code == 404


async def test_booking_with_an_area_books_once_and_records_the_tracking_id(
    client: AsyncClient, db, unique_phone: str, redx_transport: FakeProviderTransport
) -> None:
    shop = await signed_in_shop(client, unique_phone, shop_name="RedX Book")
    redx_transport.always("GET", f"{P}/pickup/stores", body=STORES_OK)
    assert (await _connect(client, shop)).status_code == 201
    order = await _order(client, shop)
    redx_transport.enqueue("POST", f"{P}/parcel", body=CREATE_OK)

    response = await client.post(
        f"/v1/couriers/orders/{order['id']}/book",
        params={"provider": "redx"},
        headers=auth_header(shop),
        json={"delivery_area_id": 12, "delivery_area_name": "Mirpur DOHS"},
    )

    assert response.status_code == 200, response.text
    [item] = response.json()["items"]
    assert item["outcome"] == str(ConsignmentStatus.BOOKED)
    assert item["tracking_code"] == TRACKING_ID
    [sent] = redx_transport.calls_to("POST", f"{P}/parcel")
    assert sent["json"]["merchant_invoice_id"] == order["order_number"]
    assert sent["json"]["delivery_area_id"] == 12
    assert sent["json"]["cash_collection_amount"] == "1250"

    # Tracked afterwards by the same id, on the adaptive schedule.
    set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(shop["tenant_id"])))
    consignment = await db.get(Consignment, uuid.UUID(item["consignment_id"]))
    assert consignment is not None
    assert consignment.provider_consignment_id == TRACKING_ID
    assert consignment.next_poll_at is not None


async def test_booking_without_an_area_is_refused_and_leaves_nothing_behind(
    client: AsyncClient, db, unique_phone: str, redx_transport: FakeProviderTransport
) -> None:
    shop = await signed_in_shop(client, unique_phone, shop_name="RedX No Area")
    redx_transport.always("GET", f"{P}/pickup/stores", body=STORES_OK)
    assert (await _connect(client, shop)).status_code == 201
    order = await _order(client, shop)

    response = await client.post(
        f"/v1/couriers/orders/{order['id']}/book",
        params={"provider": "redx"},
        headers=auth_header(shop),
        json={},
    )

    assert response.status_code == 422, response.text
    assert "delivery area" in response.json()["message_en"]
    assert redx_transport.calls_to("POST", f"{P}/parcel") == []
    set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(shop["tenant_id"])))
    rows = (
        (
            await db.execute(
                sa.select(Consignment).where(Consignment.order_id == uuid.UUID(order["id"]))
            )
        )
        .scalars()
        .all()
    )
    assert rows == []


async def test_bulk_booking_with_redx_parks_nothing_as_unknown(
    client: AsyncClient, db, unique_phone: str, redx_transport: FakeProviderTransport
) -> None:
    """Bulk carries no per-order area, so every order is refused cleanly."""
    shop = await signed_in_shop(client, unique_phone, shop_name="RedX Bulk")
    redx_transport.always("GET", f"{P}/pickup/stores", body=STORES_OK)
    assert (await _connect(client, shop)).status_code == 201
    order = await _order(client, shop)

    response = await client.post(
        "/v1/couriers/orders/book-bulk",
        params={"provider": "redx"},
        headers=auth_header(shop),
        json={"order_ids": [order["id"]]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["ambiguous"] == 0
    assert response.json()["booked"] == 0
    assert redx_transport.calls_to("POST", f"{P}/parcel") == []
    set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(shop["tenant_id"])))
    rows = (
        (
            await db.execute(
                sa.select(Consignment).where(Consignment.order_id == uuid.UUID(order["id"]))
            )
        )
        .scalars()
        .all()
    )
    assert rows == []


async def test_an_ambiguous_booking_is_never_sent_twice(
    client: AsyncClient, unique_phone: str, redx_transport: FakeProviderTransport
) -> None:
    shop = await signed_in_shop(client, unique_phone, shop_name="RedX Unknown")
    redx_transport.always("GET", f"{P}/pickup/stores", body=STORES_OK)
    assert (await _connect(client, shop)).status_code == 201
    order = await _order(client, shop)
    redx_transport.enqueue_error(
        "POST", f"{P}/parcel", ProviderAmbiguousError("read timeout", provider="redx")
    )
    payload = {"delivery_area_id": 12, "delivery_area_name": "Mirpur DOHS"}

    first = await client.post(
        f"/v1/couriers/orders/{order['id']}/book",
        params={"provider": "redx"},
        headers=auth_header(shop),
        json=payload,
    )
    assert first.json()["items"][0]["outcome"] == str(ConsignmentStatus.BOOKING_UNKNOWN)

    second = await client.post(
        f"/v1/couriers/orders/{order['id']}/book",
        params={"provider": "redx"},
        headers=auth_header(shop),
        json=payload,
    )
    assert second.status_code == 409
    assert second.json()["code"] == "BOOKING_AMBIGUOUS"
    assert len(redx_transport.calls_to("POST", f"{P}/parcel")) == 1


async def test_a_refused_booking_can_be_retried_under_the_same_reference(
    client: AsyncClient, unique_phone: str, redx_transport: FakeProviderTransport
) -> None:
    shop = await signed_in_shop(client, unique_phone, shop_name="RedX Retry")
    redx_transport.always("GET", f"{P}/pickup/stores", body=STORES_OK)
    assert (await _connect(client, shop)).status_code == 201
    order = await _order(client, shop)
    redx_transport.enqueue("POST", f"{P}/parcel", status_code=400, body='{"message":"x"}')
    redx_transport.enqueue("POST", f"{P}/parcel", body=CREATE_OK)
    payload = {"delivery_area_id": 12, "delivery_area_name": "Mirpur DOHS"}

    first = await client.post(
        f"/v1/couriers/orders/{order['id']}/book",
        params={"provider": "redx"},
        headers=auth_header(shop),
        json=payload,
    )
    assert first.json()["items"][0]["outcome"] == str(ConsignmentStatus.NOT_BOOKED)
    second = await client.post(
        f"/v1/couriers/orders/{order['id']}/book",
        params={"provider": "redx"},
        headers=auth_header(shop),
        json=payload,
    )
    assert second.json()["items"][0]["outcome"] == str(ConsignmentStatus.BOOKED)

    sent = redx_transport.calls_to("POST", f"{P}/parcel")
    assert len(sent) == 2
    assert sent[0]["json"]["merchant_invoice_id"] == sent[1]["json"]["merchant_invoice_id"]


async def test_areas_and_a_quote_are_read_for_the_booking_sheet(
    client: AsyncClient, unique_phone: str, redx_transport: FakeProviderTransport
) -> None:
    shop = await signed_in_shop(client, unique_phone, shop_name="RedX Quote")
    redx_transport.always("GET", f"{P}/pickup/stores", body=STORES_OK)
    assert (await _connect(client, shop)).status_code == 201
    order = await _order(client, shop)

    redx_transport.enqueue("GET", f"{P}/areas", body=AREAS_OK)
    areas = await client.get(
        "/v1/couriers/accounts/redx/areas",
        params={"district_name": "Dhaka"},
        headers=auth_header(shop),
    )
    assert areas.status_code == 200, areas.text
    assert areas.json()[1] == {
        "id": "2",
        "name": "Dhanmondi",
        "post_code": "1209",
        "division_name": "Dhaka",
        "zone_id": "1",
    }

    no_store = await client.get(
        f"/v1/couriers/orders/{order['id']}/quote",
        params={"provider": "redx", "delivery_area_id": 2},
        headers=auth_header(shop),
    )
    assert no_store.json()["available"] is False
    assert no_store.json()["reason"]

    chosen = await client.post(
        "/v1/couriers/accounts/redx/store",
        headers=auth_header(shop),
        json={"provider_store_id": "7", "name": "Mirpur Store"},
    )
    assert chosen.status_code == 200
    redx_transport.enqueue("GET", f"{P}/pickup/store/info/7", body=STORE_INFO)
    redx_transport.enqueue("GET", f"{P}/charge/charge_calculator", body=CHARGE_OK)
    quoted = await client.get(
        f"/v1/couriers/orders/{order['id']}/quote",
        params={"provider": "redx", "delivery_area_id": 2},
        headers=auth_header(shop),
    )
    assert quoted.json() == {
        "provider": "redx",
        "available": True,
        "delivery_fee_paisa": 6000,
        "cod_fee_paisa": 1050,
        "reason": None,
    }
    assert redx_transport.calls_to("POST", f"{P}/parcel") == []


async def test_the_callback_url_carries_a_token_only_this_shop_can_use(
    client: AsyncClient, unique_phone: str, redx_transport: FakeProviderTransport
) -> None:
    shop = await signed_in_shop(client, unique_phone, shop_name="RedX Hook")
    redx_transport.always("GET", f"{P}/pickup/stores", body=STORES_OK)
    assert (await _connect(client, shop)).status_code == 201

    setup = await client.get("/v1/couriers/accounts/redx/webhook", headers=auth_header(shop))
    assert setup.status_code == 200
    url = setup.json()["callback_url"]
    assert "/v1/webhooks/couriers/redx/" in url and "?token=" in url
    # Stable once issued, so a URL pasted into RedX keeps working.
    again = await client.get("/v1/couriers/accounts/redx/webhook", headers=auth_header(shop))
    assert again.json()["callback_url"] == url

    path = url.split("://", 1)[1].split("/", 1)[1]
    route, token = path.split("?token=")

    accepted = await client.post(f"/{route}", params={"token": token}, content=CALLBACK)
    assert accepted.status_code == 200, accepted.text
    duplicate = await client.post(f"/{route}", params={"token": token}, content=CALLBACK)
    assert duplicate.status_code == 200
    assert duplicate.json()["detail"] == "Already received."

    other_body = CALLBACK.replace(b"delivery-in-progress", b"delivered")
    wrong = await client.post(f"/{route}", params={"token": "not-it"}, content=other_body)
    assert wrong.status_code == 401
    unknown = await client.post(
        "/v1/webhooks/couriers/redx/not-a-route", params={"token": token}, content=CALLBACK
    )
    assert unknown.status_code == 202
    assert unknown.json()["processed"] is False


async def test_polling_moves_a_redx_parcel_and_settles_only_a_regular_delivery(
    client: AsyncClient, db, settings, unique_phone: str, redx_transport: FakeProviderTransport
) -> None:
    shop = await signed_in_shop(client, unique_phone, shop_name="RedX Poll")
    redx_transport.always("GET", f"{P}/pickup/stores", body=STORES_OK)
    assert (await _connect(client, shop)).status_code == 201
    order = await _order(client, shop)
    redx_transport.enqueue("POST", f"{P}/parcel", body=CREATE_OK)
    booked = await client.post(
        f"/v1/couriers/orders/{order['id']}/book",
        params={"provider": "redx"},
        headers=auth_header(shop),
        json={"delivery_area_id": 12, "delivery_area_name": "Mirpur DOHS"},
    )
    consignment_id = uuid.UUID(booked.json()["items"][0]["consignment_id"])

    set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(shop["tenant_id"])))
    accounts = CourierAccountService(db, vault=CredentialVault(settings))
    sync = StatusSyncService(
        db,
        accounts=accounts,
        consignments=ConsignmentService(db, receivables=ReceivableService(db)),
        settings=settings,
    )
    account = await accounts.for_provider("redx")
    assert account is not None

    async def poll(body: str) -> Consignment:
        redx_transport.enqueue("GET", f"{P}/parcel/info/{TRACKING_ID}", body=body)
        consignment = await db.get(Consignment, consignment_id)
        assert consignment is not None
        await sync.poll_one(consignment, account=account)
        await db.refresh(consignment)
        return consignment

    assert (await poll(_info("pickup-pending"))).consignment_status is ConsignmentStatus.BOOKED
    assert (await poll(_info("ready-for-delivery"))).consignment_status is (
        ConsignmentStatus.PICKED_UP
    )
    # A final status whose delivery type does not say which outcome it is
    # changes nothing.
    unsettled = await poll(_info("delivered", "exchange-delivery"))
    assert unsettled.consignment_status is ConsignmentStatus.PICKED_UP
    assert unsettled.provider_raw_status == "delivered"

    settled = await poll(_info("delivered", "regular"))
    assert settled.consignment_status is ConsignmentStatus.DELIVERED
    assert settled.next_poll_at is None


async def test_a_service_connect_refuses_a_second_secret_for_redx(
    db, settings, unique_phone: str, client: AsyncClient
) -> None:
    shop = await signed_in_shop(client, unique_phone, shop_name="RedX Two Slots")
    set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(shop["tenant_id"])))
    transport = FakeProviderTransport(provider="redx")
    service = CourierAccountService(
        db,
        vault=CredentialVault(settings),
        registry=CourierAdapterRegistry({"redx": lambda: _adapter(transport)}),
    )

    from app.core.errors import ValidationError

    with pytest.raises(ValidationError):
        await service.connect(ConnectRequest(provider="redx", api_key=TOKEN, secret_key="extra"))
    assert transport.calls == []
