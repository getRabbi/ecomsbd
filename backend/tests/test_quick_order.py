"""Quick-order sheet backend (V4 WP1.5).

*   the LLM path: ``captures/manual`` reads the sheet's draft once more with
    the enhanced parser when ``ai_parse_enabled`` is on (fields only);
*   the courier suggestion in its reply: the shop default, else the best
    delivered rate in the order's zone from the shop's own parcels, with the
    WP0.6 sample floor.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient

from app.consignments.models import Consignment, ConsignmentStatus
from app.core.clock import utc_now
from app.core.context import RequestContext, set_context
from app.delivery_rules.suggestion import HISTORY_WINDOW, suggest_courier
from app.orders.models import Order, OrderChannel, OrderStatus
from app.orders.parser.ai_client import FakeAiParseClient
from app.orders.zones import DeliveryZone
from app.tenants.models import Tenant
from tests.conftest_commerce import signed_in_shop
from tests.test_ai_parse import LOOSE_TEXT, ai_parse_on, answer
from tests.test_auth_flow import auth_header
from tests.test_intake_capture import CHAT, MANUAL, intake_on

__all__ = ["ai_parse_on", "intake_on"]  # fixtures used by name below

DHAKA = "House 4, Road 2, Mirpur 10, Dhaka"


async def _paste(client: AsyncClient, shop: dict[str, Any], text: str) -> dict[str, Any]:
    response = await client.post(
        MANUAL, json={"source": "SHARE", "messages": [{"text": text}]}, headers=auth_header(shop)
    )
    assert response.status_code == 200, response.text
    return response.json()


# ------------------------------------------------------------- LLM path ---


async def test_the_sheet_reads_the_draft_with_the_model_when_ai_parse_is_on(
    client: AsyncClient,
    unique_phone: str,
    intake_on: None,
    ai_parse_on: None,
    monkeypatch: pytest.MonkeyPatch,
):
    shop = await signed_in_shop(client, unique_phone)
    unsure = {**answer()["field_confidence"], "address": 0.3}
    fake = FakeAiParseClient(answer(field_confidence=unsure))
    monkeypatch.setattr("app.api.v1.intake.build_ai_parse_client", lambda settings: fake)

    body = await _paste(client, shop, LOOSE_TEXT)

    assert len(fake.calls) == 1
    draft = body["draft"]
    assert draft["customer_name"] == "sakib"
    assert draft["address"] == "chandpur sadar"
    assert draft["selected_phone"] == "+8801700000012"
    assert [item["name"] for item in draft["items"]] == ["black hoodie"]
    # The model was unsure of the address: the sheet highlights it.
    assert "address" in draft["uncertain_fields"] and "name" not in draft["uncertain_fields"]


async def test_without_ai_parse_the_draft_stays_deterministic(
    client: AsyncClient, unique_phone: str, intake_on: None, monkeypatch: pytest.MonkeyPatch
):
    shop = await signed_in_shop(client, unique_phone)
    fake = FakeAiParseClient(answer())
    monkeypatch.setattr("app.api.v1.intake.build_ai_parse_client", lambda settings: fake)

    body = await _paste(client, shop, LOOSE_TEXT)

    assert fake.calls == []
    assert body["draft"]["customer_name"] != "sakib"


async def test_a_model_outage_keeps_the_deterministic_draft(
    client: AsyncClient,
    unique_phone: str,
    intake_on: None,
    ai_parse_on: None,
    monkeypatch: pytest.MonkeyPatch,
):
    shop = await signed_in_shop(client, unique_phone)
    fake = FakeAiParseClient(error="AI_PROVIDER_TIMEOUT")
    monkeypatch.setattr("app.api.v1.intake.build_ai_parse_client", lambda settings: fake)

    body = await _paste(client, shop, LOOSE_TEXT)

    assert len(fake.calls) == 1
    assert body["draft"]["selected_phone"] == "+8801700000012"
    assert body["draft"]["status"] in ("NEEDS_INFO", "READY_FOR_REVIEW")


# ---------------------------------------------------- courier suggestion ---


async def test_the_reply_suggests_the_shops_default_courier(
    client: AsyncClient, unique_phone: str, intake_on: None, delivery_rules_on: None
):
    shop = await signed_in_shop(client, unique_phone)
    rules = await client.patch(
        "/v1/shop/delivery-rules", json={"default_courier": "pathao"}, headers=auth_header(shop)
    )
    assert rules.status_code == 200, rules.text

    body = await _paste(client, shop, "\n".join(CHAT))

    assert body["courier_suggestion"] == {
        "courier": "pathao",
        "source": "SHOP_DEFAULT",
        "zone": None,
        "delivered": None,
        "parcels": None,
    }


async def test_no_default_and_no_history_suggests_nothing(
    client: AsyncClient, unique_phone: str, intake_on: None
):
    shop = await signed_in_shop(client, unique_phone)
    body = await _paste(client, shop, "\n".join(CHAT))
    assert "courier_suggestion" in body and body["courier_suggestion"] is None


async def _tenant(db: Any) -> Tenant:
    tenant = Tenant(name="Quick Order Courier Shop")
    db.add(tenant)
    await db.flush()
    set_context(RequestContext(trace_id="test", tenant_id=tenant.id))
    return tenant


async def _parcel(
    db: Any,
    *,
    provider: str,
    status: ConsignmentStatus,
    district: str = "Dhaka",
    area: str | None = "Mirpur",
    age: timedelta = timedelta(days=3),
) -> None:
    number = f"QO-{uuid.uuid4().hex[:10]}"
    order = Order(
        order_number=number,
        client_id=uuid.uuid4(),
        status=str(OrderStatus.CONFIRMED),
        channel=str(OrderChannel.MANUAL),
        business_date=utc_now().date(),
        subtotal_paisa=100_000,
        discount_paisa=0,
        delivery_fee_paisa=0,
        cod_amount_paisa=100_000,
        delivery_district=district,
        delivery_area=area,
    )
    db.add(order)
    await db.flush()
    db.add(
        Consignment(
            order_id=order.id,
            provider=provider,
            merchant_reference=number,
            status=str(status),
            cod_amount_paisa=100_000,
            created_at=utc_now() - age,
        )
    )
    await db.flush()


@pytest.mark.asyncio
async def test_history_picks_the_best_delivered_rate_in_the_zone(db: Any) -> None:
    tenant = await _tenant(db)
    delivered, returned = ConsignmentStatus.DELIVERED, ConsignmentStatus.RETURNED
    for status in (delivered, delivered, delivered, returned):  # pathao 3/4
        await _parcel(db, provider="pathao", status=status)
    for _ in range(3):  # steadfast 3/3
        await _parcel(db, provider="steadfast", status=delivered)
    for _ in range(2):  # redx 2/2: under the sample floor, not judged
        await _parcel(db, provider="redx", status=delivered)
    # None of these count for inside Dhaka:
    for _ in range(3):
        await _parcel(db, provider="steadfast", status=returned, district="Chandpur", area=None)
    await _parcel(db, provider="steadfast", status=ConsignmentStatus.CANCELLED)
    await _parcel(db, provider="steadfast", status=ConsignmentStatus.IN_TRANSIT)
    await _parcel(db, provider="steadfast", status=returned, age=HISTORY_WINDOW + timedelta(days=1))
    await _parcel(db, provider="manual", status=delivered)

    found = await suggest_courier(db, tenant.id, address=DHAKA)

    assert found is not None
    assert found.as_dict() == {
        "courier": "steadfast",
        "source": "HISTORY",
        "zone": "INSIDE_DHAKA",
        "delivered": 3,
        "parcels": 3,
    }
    outside = await suggest_courier(db, tenant.id, address="Chandpur Sadar, Chandpur")
    # Steadfast 0/3 outside Dhaka is still the only courier with enough data.
    assert outside is not None and outside.zone == DeliveryZone.OUTSIDE_DHAKA
    assert (outside.courier, outside.delivered, outside.parcels) == ("steadfast", 0, 3)


@pytest.mark.asyncio
async def test_equal_rates_prefer_more_parcels(db: Any) -> None:
    tenant = await _tenant(db)
    for _ in range(3):
        await _parcel(db, provider="pathao", status=ConsignmentStatus.DELIVERED)
    for _ in range(5):
        await _parcel(db, provider="steadfast", status=ConsignmentStatus.DELIVERED)
    found = await suggest_courier(db, tenant.id, address=DHAKA)
    assert found is not None and (found.courier, found.parcels) == ("steadfast", 5)


@pytest.mark.asyncio
async def test_thin_history_or_an_unknown_zone_suggests_nothing(db: Any) -> None:
    tenant = await _tenant(db)
    for _ in range(2):
        await _parcel(db, provider="pathao", status=ConsignmentStatus.DELIVERED)
    assert await suggest_courier(db, tenant.id, address=DHAKA) is None
    for _ in range(3):
        await _parcel(db, provider="pathao", status=ConsignmentStatus.DELIVERED)
    assert await suggest_courier(db, tenant.id, address="somewhere near the big mosque") is None
    assert await suggest_courier(db, tenant.id, address=None) is None
