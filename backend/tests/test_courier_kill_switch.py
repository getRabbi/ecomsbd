"""The courier kill switches, as a shop and the API meet them.

Each courier has a feature flag (``steadfast_enabled``, ``pathao_enabled``,
``redx_enabled``) that defaults to off. These tests pin down two things:

* the flag decides what the provider listing says, and "on, but nothing
  saved yet" is *not connected* — never *disabled*;
* the flag is enforced on the server where new provider work starts, so a
  request sent straight to the API cannot connect or book with a courier that
  is switched off, while what a shop already saved can still be removed.

No test here enables the flags through the module-wide fixture: the off state
is the subject.
"""

from __future__ import annotations

import uuid
from typing import Any

from httpx import AsyncClient

from app.common.feature_flags import FeatureFlag, FlagKey, FlagScope
from tests.conftest_commerce import create_order, create_product, signed_in_shop
from tests.fixtures.steadfast import bodies
from tests.test_auth_flow import auth_header

COURIERS = ("steadfast", "pathao", "redx")
API_KEY = "sfk-kill-switch-key-abcd"
SECRET_KEY = "sfs-kill-switch-secret-wxyz"


async def _tenant_flag(system_db, key: FlagKey, tenant_id: str, *, enabled: bool) -> None:
    system_db.add(
        FeatureFlag(
            key=str(key),
            scope=FlagScope.TENANT,
            tenant_id=uuid.UUID(tenant_id),
            enabled=enabled,
            rollout_percentage=100 if enabled else 0,
        )
    )
    await system_db.commit()


async def _providers(client: AsyncClient, shop: dict[str, Any]) -> dict[str, dict[str, Any]]:
    response = await client.get("/v1/couriers/providers", headers=auth_header(shop))
    assert response.status_code == 200
    return {row["provider"]: row for row in response.json()}


async def _connect_steadfast(client: AsyncClient, shop: dict[str, Any]) -> Any:
    return await client.post(
        "/v1/couriers/accounts/steadfast/connect",
        headers=auth_header(shop),
        json={"credentials": {"api_key": API_KEY, "secret_key": SECRET_KEY}},
    )


async def test_without_flag_rows_every_courier_is_off(
    client: AsyncClient, unique_phone: str
) -> None:
    """The production state before 2026-10-01: no rows, so every shop saw Disabled."""
    shop = await signed_in_shop(client, unique_phone, shop_name="No Rows Shop")
    providers = await _providers(client, shop)

    for courier in COURIERS:
        assert providers[courier]["enabled"] is False
        assert providers[courier]["connect_form"] is not None
    assert providers["manual"]["enabled"] is True


async def test_switched_on_and_nothing_saved_reads_not_connected(
    client: AsyncClient, unique_phone: str, courier_flags_on: None
) -> None:
    shop = await signed_in_shop(client, unique_phone, shop_name="Fresh Shop")
    providers = await _providers(client, shop)

    for courier in COURIERS:
        assert providers[courier]["enabled"] is True
        assert providers[courier]["connect_form"]["fields"], "the connect form is declared"

    accounts = await client.get("/v1/couriers/accounts", headers=auth_header(shop))
    assert accounts.status_code == 200 and accounts.json() == [], "nothing is falsely connected"

    bookable = await client.get("/v1/couriers/bookable", headers=auth_header(shop))
    assert bookable.status_code == 200
    reasons = {row["provider"]: row for row in bookable.json()}
    for courier in COURIERS:
        assert reasons[courier]["bookable"] is False
        assert reasons[courier]["reason"] == "NOT_CONNECTED"


async def test_connecting_a_switched_off_courier_is_refused_before_any_provider_call(
    client: AsyncClient, unique_phone: str
) -> None:
    # The suite-wide registry raises if an adapter is built, so a 403 here
    # (rather than a 500) also proves the provider was never contacted.
    shop = await signed_in_shop(client, unique_phone, shop_name="Off Shop")

    response = await _connect_steadfast(client, shop)

    assert response.status_code == 403
    body = response.json()
    assert body["code"] == "FEATURE_DISABLED"
    assert body["details"] == {"provider": "steadfast", "reason": "NOT_ENABLED"}
    assert API_KEY not in response.text and SECRET_KEY not in response.text
    accounts = await client.get("/v1/couriers/accounts", headers=auth_header(shop))
    assert accounts.json() == [], "nothing was stored"


async def test_a_shop_override_off_beats_the_global_switch(
    client: AsyncClient, unique_phone: str, system_db, courier_flags_on: None
) -> None:
    shop = await signed_in_shop(client, unique_phone, shop_name="Override Shop")
    await _tenant_flag(system_db, FlagKey.PATHAO_ENABLED, shop["tenant_id"], enabled=False)

    providers = await _providers(client, shop)
    assert providers["pathao"]["enabled"] is False
    assert providers["steadfast"]["enabled"] is True

    response = await client.post(
        "/v1/couriers/accounts/pathao/connect",
        headers=auth_header(shop),
        json={"credentials": {"client_id": "cid-1", "client_secret": "cs-1"}},
    )
    assert response.status_code == 403 and response.json()["code"] == "FEATURE_DISABLED"


async def test_switching_a_courier_off_stops_booking_but_not_disconnecting(
    client: AsyncClient,
    unique_phone: str,
    system_db,
    courier_flags_on: None,
    steadfast_transport,
) -> None:
    shop = await signed_in_shop(client, unique_phone, shop_name="Switched Off Later")
    steadfast_transport.enqueue("GET", "/get_balance", body=bodies.BALANCE_OK)
    connected = await _connect_steadfast(client, shop)
    assert connected.status_code == 201 and connected.json()["result"] == "VALID"

    product = await create_product(client, shop, name="Kill", sku=f"KS-{uuid.uuid4().hex[:6]}")
    order = await create_order(
        client,
        shop,
        items=[{"product_id": product["id"], "quantity": 1, "unit_price_paisa": 90_000}],
        cod_amount_paisa=90_000,
        address="House 4, Road 2, Dhanmondi, Dhaka",
    )
    order_id = order["order"]["id"]

    await _tenant_flag(system_db, FlagKey.STEADFAST_ENABLED, shop["tenant_id"], enabled=False)

    booked = await client.post(
        f"/v1/couriers/orders/{order_id}/book?provider=steadfast",
        headers=auth_header(shop),
        json={},
    )
    assert booked.status_code == 403 and booked.json()["code"] == "FEATURE_DISABLED"
    bulk = await client.post(
        "/v1/couriers/orders/book-bulk?provider=steadfast",
        headers=auth_header(shop),
        json={"order_ids": [order_id]},
    )
    assert bulk.status_code == 403 and bulk.json()["code"] == "FEATURE_DISABLED"
    quote = await client.get(
        f"/v1/couriers/orders/{order_id}/quote?provider=steadfast", headers=auth_header(shop)
    )
    assert quote.status_code == 403
    areas = await client.get("/v1/couriers/accounts/steadfast/areas", headers=auth_header(shop))
    assert areas.status_code == 403
    assert steadfast_transport.calls_to("POST", "/create_order") == []
    assert steadfast_transport.calls_to("POST", "/create_order/bulk-order") == []

    bookable = await client.get("/v1/couriers/bookable", headers=auth_header(shop))
    steadfast = next(row for row in bookable.json() if row["provider"] == "steadfast")
    assert steadfast["reason"] == "NOT_ENABLED"

    removed = await client.delete("/v1/couriers/accounts/steadfast", headers=auth_header(shop))
    assert removed.status_code == 200
    assert removed.json()["status"] == "DISCONNECTED"
