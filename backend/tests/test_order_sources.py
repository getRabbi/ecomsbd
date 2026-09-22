import uuid

import pytest
import sqlalchemy as sa

from app.db.session import system_session
from app.tenants.models import TenantUser
from tests.conftest_commerce import signed_in_shop
from tests.test_auth_flow import auth_header


async def source(client, phone, **extra):
    shop = await signed_in_shop(client, phone)
    headers = auth_header(shop)
    response = await client.post(
        "/v1/order-sources", headers=headers, json={"name": "Own store", "enabled": True, **extra}
    )
    assert response.status_code == 201, response.text
    return response.json(), headers, shop


ORDER = {
    "phone": "01712345678",
    "customer_name": "Buyer",
    "items": [{"name": "Shirt", "quantity": 1, "unit_price_paisa": 12000}],
}


async def test_native_order_is_durable_and_conflicts_never_overwrite(client, unique_phone):
    row, headers, _ = await source(client, unique_phone)
    path = f"/v1/order-sources/{row['id']}/ingest"
    body = {"external_order_id": "shop-123", "payload": ORDER}
    response = await client.post(path, headers=headers, json=body)
    assert response.status_code == 201, response.text
    replay = await client.post(path, headers=headers, json=body)
    assert replay.json()["replayed"]
    assert replay.json()["order_id"] == response.json()["order_id"]
    order = (await client.get("/v1/orders/" + response.json()["order_id"], headers=headers)).json()
    assert order["status"] == "DRAFT"
    assert order["channel"] == "API"
    assert order["cod_amount_paisa"] == 12000
    changed = await client.post(
        path, headers=headers, json={**body, "payload": {**ORDER, "note": "changed"}}
    )
    assert changed.status_code == 409
    await client.patch("/v1/order-sources/" + row["id"], headers=headers, json={"enabled": False})
    assert (await client.post(path, headers=headers, json=body)).json()["replayed"]
    assert (
        await client.post(path, headers=headers, json={**body, "external_order_id": "new"})
    ).status_code == 409


async def test_reuses_import_mapping_and_normalizes_bangla(client, unique_phone):
    row, headers, _ = await source(
        client, unique_phone, mapping={"phone": "মোবাইল", "product": "পণ্য", "amount": "টাকা"}
    )
    path = f"/v1/order-sources/{row['id']}/ingest"
    body = {
        "external_order_id": "mapped-1",
        "payload": {"মোবাইল": "০১৭১২৩৪৫৬৭৮", "পণ্য": "জামা", "টাকা": "১২৫.৫০"},
    }
    result = await client.post(path, headers=headers, json=body)
    assert result.status_code == 201, result.text
    order = (await client.get("/v1/orders/" + result.json()["order_id"], headers=headers)).json()
    assert order["cod_amount_paisa"] == 12550
    bad = {**body, "external_order_id": "invalid", "payload": {**body["payload"], "টাকা": "bad"}}
    assert (await client.post(path, headers=headers, json=bad)).status_code == 422
    bad["payload"]["টাকা"] = "100"
    assert (await client.post(path, headers=headers, json=bad)).status_code == 201


async def test_tenant_and_role_boundaries(client, unique_phone):
    row, headers, shop = await source(client, unique_phone)
    other = await signed_in_shop(client, "018" + unique_phone[3:])
    path = f"/v1/order-sources/{row['id']}/ingest"
    body = {"external_order_id": "one", "payload": ORDER}
    assert (await client.post(path, headers=auth_header(other), json=body)).status_code == 404
    async with system_session("test role") as db:
        member = await db.scalar(
            sa.select(TenantUser).where(TenantUser.tenant_id == uuid.UUID(shop["tenant_id"]))
        )
        member.role = "VIEWER"
    assert (await client.post(path, headers=headers, json=body)).status_code == 403
    assert (
        await client.post("/v1/order-sources", headers=headers, json={"name": "new"})
    ).status_code == 403


@pytest.mark.parametrize("provider", ["SHOPIFY", "WOOCOMMERCE", "MESSENGER"])
async def test_provider_contract_gate(client, unique_phone, provider):
    shop = await signed_in_shop(client, unique_phone)
    result = await client.post(
        "/v1/order-sources",
        headers=auth_header(shop),
        json={"name": "provider", "provider": provider, "enabled": True},
    )
    assert result.status_code == 409
    assert result.json()["details"]["blocker"] == "ORDER_SOURCE_OFFICIAL_CONTRACT_REQUIRED"


async def test_external_identity_cannot_override_an_existing_client_id(client, unique_phone):
    row, headers, _ = await source(client, unique_phone)
    result = await client.post(
        f"/v1/order-sources/{row['id']}/ingest",
        headers=headers,
        json={"external_order_id": "one", "payload": {**ORDER, "client_id": str(uuid.uuid4())}},
    )
    assert result.status_code == 422
