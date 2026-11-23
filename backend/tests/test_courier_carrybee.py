"""Carrybee courier adapter (V4 WP7.2).

Fake HTTP only (``FakeProviderTransport``); numbers are ``017000000xx``.
Contract: ``docs/providers/carrybee/CONTRACT.md``.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient

from app.common.money import BDT, Money
from app.consignments.models import ConsignmentStatus
from app.core.context import RequestContext, set_context
from app.couriers.adapter import BookingOutcome, BookingRequest, Unavailable
from app.couriers.capabilities import CapabilityState, load_manifest
from app.couriers.carrybee.adapter import (
    SUPPORTED_CAPABILITIES,
    CarrybeeAdapter,
    build_create_payload,
)
from app.couriers.carrybee.client import CarrybeeClient, CarrybeeConfig, CarrybeeCredentials
from app.couriers.carrybee.contract import LIVE_BASE_URL, SANDBOX_BASE_URL, WEBHOOK_EVENTS
from app.couriers.carrybee.mapping import STATUS_MAP, assert_map_is_complete
from app.couriers.carrybee.webhooks import CarrybeeWebhookVerifier, parse_carrybee_events
from app.couriers.credentials import build_credentials, pack_secondary, spec_for
from app.couriers.http import (
    FakeProviderTransport,
    ProviderAmbiguousError,
    ProviderUnavailableError,
)
from app.couriers.models import CourierAccount, CredentialValidation
from app.couriers.registry import CourierAdapterRegistry, set_courier_registry
from app.couriers.status_maps import map_courier_status
from tests.conftest_commerce import signed_in_shop
from tests.test_auth_flow import auth_header

pytestmark = pytest.mark.usefixtures("courier_flags_on")

CREDS = CarrybeeCredentials(
    client_id="cb-client-id-0001",
    client_secret="cb-client-secret-not-real",
    client_context="cb-context-not-real",
)
SECRET = "cb-webhook-secret-not-real-0001"
STORES_OK = json.dumps(
    {
        "error": False,
        "message": "Store list",
        "data": {
            "stores": [
                {
                    "id": "st-1",
                    "name": "Main store",
                    "address": "House 5, Road 3, Banani",
                    "city_id": 14,
                    "zone_id": 10,
                    "area_id": 100,
                    "is_active": True,
                    "is_approved": True,
                    "is_default_pickup_store": True,
                }
            ]
        },
    }
)
CREATED = json.dumps(
    {
        "error": False,
        "message": "Order created successfully",
        "data": {
            "order": {
                "consignment_id": "FX1212124433",
                "store_id": "st-1",
                "merchant_order_id": "ECB-1",
                "collectable_amount": "1250",
                "cod_fee": 15,
                "delivery_fee": "60",
            }
        },
    }
)


def _adapter(transport: FakeProviderTransport) -> CarrybeeAdapter:
    async def no_sleep(_: float) -> None:
        return None

    return CarrybeeAdapter(
        CarrybeeClient(transport, config=CarrybeeConfig(retry_backoff_seconds=0), sleep=no_sleep)
    )


def _transport() -> FakeProviderTransport:
    return FakeProviderTransport(provider="carrybee", base_url=LIVE_BASE_URL)


def _request(**overrides: Any) -> BookingRequest:
    values: dict[str, Any] = {
        "order_id": uuid.uuid4(),
        "merchant_reference": "ECB-1",
        "recipient_name": "Karim",
        "recipient_phone_e164": "+8801700000021",
        "recipient_address": "House 5, Road 3, Banani, Dhaka",
        "cod_amount": Money(125_050, BDT),
        "item_description": "One shirt",
        "store_reference": "st-1",
    }
    values.update(overrides)
    return BookingRequest(**values)


# ------------------------------------------------------------- payload ---


def test_the_create_body_is_the_documented_one() -> None:
    payload = build_create_payload(_request(note="Call first", weight_grams=800), "ECB-1")
    assert payload == {
        "store_id": "st-1",
        "merchant_order_id": "ECB-1",
        "delivery_type": 1,
        "product_type": 1,
        "recipient_phone": "01700000021",
        "recipient_name": "Karim",
        "recipient_address": "House 5, Road 3, Banani, Dhaka",
        "item_weight": 800,
        "item_quantity": 1,
        "collectable_amount": 1251,  # whole Taka; the paisa remainder is recorded
        "special_instruction": "Call first",
        "product_description": "One shirt",
    }
    assert "city_id" not in payload and "is_exchange" not in payload
    default = build_create_payload(
        _request(recipient_alternate_phone_e164="+8801700000022"), "ECB-2"
    )
    assert default["item_weight"] == 500 and default["recipient_secendary_phone"] == "01700000022"


@pytest.mark.parametrize(
    "overrides",
    [
        {"store_reference": None},
        {"recipient_address": "Banani"},  # < 10
        {"recipient_address": "x" * 201},  # > 200: refused, never cut
        {"recipient_phone_e164": "+14155550100"},
        {"cod_amount": Money(10_000_100, BDT)},  # > 100,000 Taka
        {"weight_grams": 25_001},
        {"item_quantity": 201},
        {"recipient_name": "K"},
    ],
)
def test_a_booking_carrybee_would_refuse_is_refused_before_sending(
    overrides: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        build_create_payload(_request(**overrides), "ECB-1")
    with pytest.raises(ValueError):
        build_create_payload(_request(), "E" * 50)  # merchant_order_id < 50


def test_the_preview_masks_both_numbers_and_splits_the_cod() -> None:
    preview = _adapter(_transport()).describe_booking(
        _request(recipient_alternate_phone_e164="+8801700000022"), "ECB-1"
    )
    assert "01700000021" not in json.dumps(preview.redacted_payload)
    assert "01700000022" not in json.dumps(preview.redacted_payload)
    assert (preview.cod_taka, preview.cod_residual_paisa) == (1251, -50)


# ------------------------------------------------------------- calls ---


async def test_credentials_are_checked_with_the_store_list_and_three_headers() -> None:
    transport = _transport()
    transport.enqueue("GET", "/api/v2/stores", body=STORES_OK)
    result = await _adapter(transport).validate_credentials(CREDS)
    assert result.valid and result.account_label == "****0001"
    (call,) = transport.calls
    assert call["url"] == f"{LIVE_BASE_URL}/api/v2/stores"
    assert call["headers"]["Client-ID"] == CREDS.client_id
    assert call["headers"]["Client-Secret"] == CREDS.client_secret
    assert call["headers"]["Client-Context"] == CREDS.client_context
    assert "cb-client-secret" not in repr(CREDS) and "cb-context" not in str(CREDS)

    refused = _transport()
    refused.always("GET", "/api/v2/stores", status_code=401, body='{"error": true}')
    rejected = await _adapter(refused).validate_credentials(CREDS)
    assert rejected.rejected and not rejected.valid

    down = _transport()
    down.always("GET", "/api/v2/stores", status_code=503, body="")
    unsure = await _adapter(down).validate_credentials(CREDS)
    assert unsure.is_inconclusive and len(down.calls) == 3  # reads are retried

    sandbox = _transport()
    sandbox.always("GET", f"{SANDBOX_BASE_URL}/api/v2/stores", body=STORES_OK)
    sandbox_creds = CarrybeeCredentials("id-0002", "s", "c", sandbox=True)
    assert (await _adapter(sandbox).validate_credentials(sandbox_creds)).valid
    assert sandbox.calls[0]["url"].startswith(SANDBOX_BASE_URL)


async def test_the_store_list_is_the_pickup_picker() -> None:
    transport = _transport()
    transport.enqueue("GET", "/api/v2/stores", body=STORES_OK)
    stores = await _adapter(transport).list_stores(CREDS)
    assert not isinstance(stores, Unavailable)
    (store,) = stores
    assert (store.provider_store_id, store.name) == ("st-1", "Main store")
    assert store.raw["is_approved"] is True


async def test_a_create_is_booked_once_and_keeps_the_consignment_id() -> None:
    transport = _transport()
    transport.enqueue("POST", "/api/v2/orders", status_code=201, body=CREATED)
    result = await _adapter(transport).create_consignment(CREDS, _request(), "ECB-1")
    assert result.outcome is BookingOutcome.BOOKED and result.consignment is not None
    assert result.consignment.provider_consignment_id == "FX1212124433"
    assert result.consignment.charge is None  # fees are Taka strings: not converted here
    (call,) = transport.calls_to("POST", "/api/v2/orders")
    assert call["idempotent"] is False and call["json"]["merchant_order_id"] == "ECB-1"


@pytest.mark.parametrize(
    ("script", "outcome"),
    [
        ({"status_code": 422, "body": '{"error": true, "message": "Validation error"}'}, "FAILED"),
        ({"status_code": 401, "body": '{"error": true}'}, "FAILED"),
        ({"status_code": 500, "body": '{"error": true}'}, "UNKNOWN"),
        ({"status_code": 201, "body": "<html>gateway</html>"}, "UNKNOWN"),
        ({"status_code": 201, "body": '{"error": true, "message": "?"}'}, "UNKNOWN"),
        ({"status_code": 201, "body": '{"error": false, "data": {"order": {}}}'}, "UNKNOWN"),
    ],
)
async def test_only_a_clear_refusal_fails_a_create(script: dict[str, Any], outcome: str) -> None:
    transport = _transport()
    transport.enqueue("POST", "/api/v2/orders", **script)
    result = await _adapter(transport).create_consignment(CREDS, _request(), "ECB-1")
    assert result.outcome is BookingOutcome(outcome)
    assert len(transport.calls_to("POST", "/api/v2/orders")) == 1  # never re-sent


async def test_a_lost_create_answer_is_unknown_and_a_refused_connection_is_failed() -> None:
    lost = _transport()
    lost.enqueue_error(
        "POST", "/api/v2/orders", ProviderAmbiguousError("no answer", provider="carrybee")
    )
    assert (
        await _adapter(lost).create_consignment(CREDS, _request(), "ECB-1")
    ).outcome is BookingOutcome.UNKNOWN

    never_sent = _transport()
    never_sent.enqueue_error(
        "POST",
        "/api/v2/orders",
        ProviderUnavailableError("refused", provider="carrybee", reached_provider=False),
    )
    assert (
        await _adapter(never_sent).create_consignment(CREDS, _request(), "ECB-1")
    ).outcome is BookingOutcome.FAILED

    local = _transport()
    refused = await _adapter(local).create_consignment(CREDS, _request(store_reference=None), "X")
    assert refused.outcome is BookingOutcome.FAILED and local.calls == []


async def test_status_keeps_an_unpublished_value_verbatim_and_claims_nothing() -> None:
    transport = _transport()
    details = {
        "error": False,
        "data": {
            "transfer_status": "Delivered",
            "consignment_id": "FX1212124433",
            "merchant_order_id": "ECB-1",
            "recipient_phone": "01700000021",
            "recipient_name": "Karim",
            "updated_at": "2026-11-23T10:11:12+06:00",
        },
    }
    transport.enqueue("GET", "/api/v2/orders/ECB-1/details", body=json.dumps(details))
    status = await _adapter(transport).get_status(CREDS, "ECB-1")
    assert not isinstance(status, Unavailable)
    assert status.raw_status == "Delivered" and status.normalized_status is None
    assert status.provider_consignment_id == "FX1212124433" and status.updated_at is not None
    assert "recipient_phone" not in status.raw and "recipient_name" not in status.raw

    details["data"]["transfer_status"] = "order.delivered"
    transport.enqueue("GET", "/api/v2/orders/FX1/details", body=json.dumps(details))
    named = await _adapter(transport).get_status(CREDS, "FX1")
    assert not isinstance(named, Unavailable)
    assert named.normalized_status == str(ConsignmentStatus.DELIVERED)


async def test_cancel_sends_the_required_reason_once() -> None:
    transport = _transport()
    transport.enqueue(
        "POST",
        "/api/v2/orders/FX1/cancel",
        status_code=202,
        body='{"error": false, "message": "Cancellation confirmed", "data": null}',
    )
    assert await _adapter(transport).cancel(CREDS, "FX1") is True
    (call,) = transport.calls
    assert call["json"]["cancellation_reason"] and len(call["json"]["cancellation_reason"]) < 200

    lost = _transport()
    lost.enqueue("POST", "/api/v2/orders/FX1/cancel", status_code=502, body="")
    with pytest.raises(ProviderAmbiguousError):
        await _adapter(lost).cancel(CREDS, "FX1")
    assert len(lost.calls) == 1


async def test_what_the_page_does_not_offer_is_unavailable() -> None:
    adapter = _adapter(_transport())
    assert isinstance(await adapter.quote(CREDS, _request()), Unavailable)
    assert isinstance(await adapter.get_balance(CREDS), Unavailable)
    assert isinstance(await adapter.customer_stats(CREDS, "+8801700000021"), Unavailable)
    assert isinstance(await adapter.request_return(CREDS, "FX1", "x"), Unavailable)
    (bulk,) = await adapter.create_bulk(CREDS, [_request()])  # type: ignore[misc]
    assert bulk.outcome is BookingOutcome.FAILED and adapter.bulk_chunk_size == 1


# ------------------------------------------------ mapping + manifest ---


def test_every_documented_event_is_mapped_and_nothing_else() -> None:
    assert_map_is_complete()
    assert len(STATUS_MAP) == len(WEBHOOK_EVENTS) == 25
    delivered = map_courier_status("carrybee", "order.delivered")
    assert delivered.canonical is ConsignmentStatus.DELIVERED and delivered.is_final
    partial = map_courier_status("carrybee", "order.partial-delivery")
    assert partial.needs_quantity_resolution and partial.is_final
    for unclear in ("order.delivery-failed", "order.paid-return", "order.paid", "order.exchange"):
        assert map_courier_status("carrybee", unclear).canonical is None
    unknown = map_courier_status("carrybee", "Delivered")
    assert unknown.canonical is None and not unknown.is_documented


def test_the_manifest_claims_exactly_what_the_adapter_does() -> None:
    manifest = load_manifest("carrybee")
    assert manifest is not None and manifest.base_url == LIVE_BASE_URL
    supported = {
        cap for cap, state in manifest.capabilities.items() if state is CapabilityState.SUPPORTED
    }
    assert supported == set(SUPPORTED_CAPABILITIES)


def test_three_values_fit_two_sealed_slots() -> None:
    spec = spec_for("carrybee")
    assert spec is not None and spec.requires_store and spec.uses_webhook
    assert [field.name for field in spec.fields] == ["client_id", "client_secret", "client_context"]
    values = {"client_id": "id-1", "client_secret": "sec-1", "client_context": "ctx-1"}
    secondary = pack_secondary(spec, values)
    creds = build_credentials(
        "carrybee", primary="id-1", secondary=secondary, config={"sandbox": True}
    )
    assert creds == CarrybeeCredentials("id-1", "sec-1", "ctx-1", sandbox=True)


# ------------------------------------------------------------ webhooks ---


def test_the_webhook_secret_is_compared_and_nothing_is_trusted_without_it() -> None:
    header = "x-cb-webhook-integration-header"
    assert not CarrybeeWebhookVerifier(None).verify(headers={header: SECRET}, body=b"{}")
    assert not CarrybeeWebhookVerifier(SECRET).verify(headers={header: "wrong"}, body=b"{}")
    assert not CarrybeeWebhookVerifier(SECRET).verify(headers={}, body=b"{}")
    assert CarrybeeWebhookVerifier(SECRET).verify(headers={header: f" {SECRET} "}, body=b"{}")


def test_events_are_parsed_without_the_rider_number() -> None:
    body = json.dumps(
        {
            "event": "order.assigned-for-delivery",
            "store_id": "st-1",
            "consignment_id": "FX1",
            "merchant_order_id": "ECB-1",
            "timestamptz": "2026-11-23T10:11:12+00:00",
            "attempt": 1,
            "agent_name": "Rahim",
            "agent_phone": "01700000023",
        }
    ).encode()
    (event,) = parse_carrybee_events(body)
    assert event.raw_status == "order.assigned-for-delivery" and event.occurred_at is not None
    assert "agent_phone" not in event.raw
    failed = parse_carrybee_events(
        b'{"event": "order.create-failed", "merchant_order_id": "ECB-2", "timestamptz": "2026-11-23T10:11:12"}'
    )
    assert failed[0].provider_consignment_id is None and failed[0].occurred_at is None
    assert (
        parse_carrybee_events(b"not json") == [] and parse_carrybee_events(b'{"event": "x"}') == []
    )


# ------------------------------------------------------------- the API ---


@pytest.fixture
def carrybee_transport() -> FakeProviderTransport:
    transport = _transport()
    set_courier_registry(CourierAdapterRegistry({"carrybee": lambda: _adapter(transport)}))
    return transport


async def test_connect_stores_ciphertext_and_the_webhook_answers_only_a_verified_call(
    client: AsyncClient, db: Any, unique_phone: str, carrybee_transport: FakeProviderTransport
) -> None:
    shop = await signed_in_shop(client, unique_phone, shop_name="Carrybee Shop")
    missing = await client.post(
        "/v1/couriers/accounts/carrybee/connect",
        headers=auth_header(shop),
        json={"credentials": {"client_id": CREDS.client_id, "client_secret": "x"}},
    )
    assert missing.status_code == 422, missing.text

    carrybee_transport.always("GET", "/api/v2/stores", body=STORES_OK)
    response = await client.post(
        "/v1/couriers/accounts/carrybee/connect",
        headers=auth_header(shop),
        json={
            "credentials": {
                "client_id": CREDS.client_id,
                "client_secret": CREDS.client_secret,
                "client_context": CREDS.client_context,
            },
            "webhook_secret": SECRET,
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["result"] == str(CredentialValidation.VALID)
    for value in (CREDS.client_secret, CREDS.client_context, SECRET):
        assert value not in response.text

    setup = await client.get("/v1/couriers/accounts/carrybee/webhook", headers=auth_header(shop))
    assert setup.status_code == 200, setup.text  # the seller opens the callback URL
    set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(shop["tenant_id"])))
    account = (
        await db.execute(sa.select(CourierAccount).where(CourierAccount.provider == "carrybee"))
    ).scalar_one()
    for value in (CREDS.client_secret, CREDS.client_context):
        assert value not in (account.secret_key_encrypted or "")
    assert account.webhook_token

    url = f"/v1/webhooks/couriers/carrybee/{account.webhook_token}"
    event = json.dumps(
        {
            "event": "order.picked",
            "store_id": "st-1",
            "consignment_id": "FX1",
            "merchant_order_id": "ECB-1",
            "timestamptz": "2026-11-23T10:11:12+00:00",
        }
    )
    wrong = await client.post(
        url, content=event, headers={"X-CB-Webhook-Integration-Header": "wrong"}
    )
    assert wrong.status_code == 401
    assert "x-cb-webhook-integration-header" not in wrong.headers

    ok = await client.post(url, content=event, headers={"X-CB-Webhook-Integration-Header": SECRET})
    assert ok.status_code == 202, ok.text
    assert ok.headers["x-cb-webhook-integration-header"] == SECRET

    stranger = await client.post(
        "/v1/webhooks/couriers/carrybee/not-a-token",
        content=event,
        headers={"X-CB-Webhook-Integration-Header": SECRET},
    )
    assert stranger.status_code == 202 and "x-cb-webhook-integration-header" not in stranger.headers
