import uuid

import sqlalchemy as sa

from app.customers.external_risk import BLOCKER, configured_provider
from app.db.session import system_session
from app.tenants.models import TenantUser
from tests.conftest_commerce import create_order, signed_in_shop
from tests.test_auth_flow import auth_header


async def test_external_risk_gated_and_first_party_unchanged(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone, plan="GROWTH")
    order = (await create_order(client, shop))["order"]
    headers = auth_header(shop)
    before = await client.get("/v1/customers/" + order["customer_id"], headers=headers)
    external = await client.get(
        "/v1/external-risk/customers/" + order["customer_id"], headers=headers
    )
    assert external.status_code == 200
    assert external.json()["blocker"] == BLOCKER
    assert external.json()["facts"] == []
    assert external.json()["provider"] is None
    assert external.json()["message_en"] and external.json()["message_bn"]
    assert configured_provider() is None
    after = await client.get("/v1/customers/" + order["customer_id"], headers=headers)
    assert before.json() == after.json()


async def test_external_risk_tenant_and_permission_boundaries(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    order = (await create_order(client, shop))["order"]
    other = await signed_in_shop(client, "018" + unique_phone[3:])
    assert (
        await client.get(
            "/v1/external-risk/customers/" + order["customer_id"], headers=auth_header(other)
        )
    ).status_code == 404
    async with system_session("test external risk permission") as db:
        member = await db.scalar(
            sa.select(TenantUser).where(TenantUser.tenant_id == uuid.UUID(shop["tenant_id"]))
        )
        member.role = "VIEWER"
    assert (
        await client.get("/v1/external-risk/capability", headers=auth_header(shop))
    ).status_code == 403
