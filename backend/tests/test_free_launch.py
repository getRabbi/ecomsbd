"""Launch override through real services and HTTP, without weakening other gates."""

import uuid

import pytest
import sqlalchemy as sa

from app.core.errors import EntitlementRequiredError, RateLimitedError
from app.db.session import system_session
from app.entitlements.catalog import UNLIMITED, Entitlement, PlanCode
from app.entitlements.service import EntitlementService
from app.tenants.models import TenantUser
from tests.conftest_commerce import create_order, signed_in_shop
from tests.test_auth_flow import auth_header
from tests.test_entitlements import _subscription, _tenant


@pytest.fixture(autouse=True)
def launch(settings, monkeypatch):
    monkeypatch.setattr(settings, "free_launch_mode", True)
    monkeypatch.setattr(settings, "billing_enabled", False)


@pytest.mark.parametrize("plan", list(PlanCode))
async def test_full_access_preserves_every_stored_plan(system_db, settings, plan):
    tenant = await _tenant(system_db)
    stored = _subscription(tenant_id=tenant, plan_code=plan)
    system_db.add(stored)
    await system_db.flush()
    before = (stored.plan_code, stored.status, stored.source, stored.current_period_end)
    service = EntitlementService(system_db, settings=settings)
    snapshot = await service.snapshot(tenant)
    assert snapshot.plan == plan
    assert snapshot.free_launch_mode and snapshot.effective_access == "full_access"
    assert not snapshot.billing_enabled
    for key in Entitlement:
        assert await service.is_allowed(tenant, key), key
    for key in (
        Entitlement.ORDERS_MONTHLY_LIMIT,
        Entitlement.TEAM_MEMBER_LIMIT,
        Entitlement.COURIER_ACCOUNT_LIMIT,
        Entitlement.PROFIT_HISTORY_DAYS,
        Entitlement.AI_PARSE_MONTHLY,
        Entitlement.SMS_SEGMENTS_MONTHLY,
    ):
        assert await service.limit(tenant, key) == UNLIMITED
    await service.require_within_limit(tenant, Entitlement.TEAM_MEMBER_LIMIT, current_count=100)
    usage = await service.consume(tenant, Entitlement.ORDERS_MONTHLY_LIMIT, amount=2000)
    assert usage.used == 2000 and usage.limit == UNLIMITED
    await system_db.refresh(stored)
    assert (stored.plan_code, stored.status, stored.source, stored.current_period_end) == before


async def test_safety_cap_remains_and_reversal_reuses_usage(system_db, settings, monkeypatch):
    tenant = await _tenant(system_db)
    service = EntitlementService(system_db, settings=settings)
    monkeypatch.setattr(settings, "risk_checks_daily_safety_limit", 2)
    await service.consume(tenant, Entitlement.RISK_CHECKS_DAILY, amount=2)
    with pytest.raises(RateLimitedError):
        await service.consume(tenant, Entitlement.RISK_CHECKS_DAILY)
    await service.consume(tenant, Entitlement.ORDERS_MONTHLY_LIMIT, amount=21)
    monkeypatch.setattr(settings, "free_launch_mode", False)
    with pytest.raises(EntitlementRequiredError):
        await service.require(tenant, Entitlement.ADVANCED_PROFIT)
    with pytest.raises(EntitlementRequiredError):
        await service.consume(tenant, Entitlement.ORDERS_MONTHLY_LIMIT)
    snapshot = await service.snapshot(tenant, include_usage=True)
    assert not snapshot.free_launch_mode and snapshot.effective_access == "plan"
    orders = next(row for row in snapshot.usage if row.entitlement == "orders_monthly_limit")
    assert orders.used == 21 and orders.limit == 20


async def test_free_seller_402_bypass_and_configuration_reversal(
    client, unique_phone, settings, monkeypatch
):
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    snapshot = (await client.get("/v1/billing/entitlements", headers=headers)).json()
    assert snapshot["plan"] == "free" and snapshot["effective_access"] == "full_access"
    assert snapshot["free_launch_mode"] is True and snapshot["billing_enabled"] is False
    for path in ("/v1/analytics/returns", "/v1/analytics/products", "/v1/analytics/profit?days=30"):
        response = await client.get(path, headers=headers)
        assert response.status_code == 200, response.text
    assert (await client.post("/v1/reconciliation/scan", headers=headers)).status_code == 200
    monkeypatch.setattr(settings, "free_launch_mode", False)
    assert (await client.get("/v1/analytics/products", headers=headers)).status_code == 402
    assert (await client.post("/v1/reconciliation/scan", headers=headers)).status_code == 402


async def test_auth_rbac_and_tenant_isolation_remain(client, unique_phone):
    assert (await client.get("/v1/billing/entitlements")).status_code == 401
    assert (await client.get("/v1/analytics/products")).status_code == 401
    owner = await signed_in_shop(client, unique_phone)
    order = (await create_order(client, owner))["order"]
    other = await signed_in_shop(client, "018" + unique_phone[3:])
    assert (
        await client.get(f"/v1/orders/{order['id']}", headers=auth_header(other))
    ).status_code == 404
    async with system_session("test free-launch RBAC") as db:
        member = await db.scalar(
            sa.select(TenantUser).where(TenantUser.tenant_id == uuid.UUID(owner["tenant_id"]))
        )
        member.role = "VIEWER"
    assert (
        await client.post("/v1/reconciliation/scan", headers=auth_header(owner))
    ).status_code == 403
    assert (
        await client.get("/v1/analytics/products", headers=auth_header(owner))
    ).status_code == 403


async def test_external_setup_and_billing_gates_remain(client, unique_phone, settings, monkeypatch):
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    order = (await create_order(client, shop))["order"]
    external = await client.get(
        f"/v1/external-risk/customers/{order['customer_id']}", headers=headers
    )
    assert external.status_code == 200 and external.json()["facts"] == []
    assert external.json()["blocker"]
    hub = (await client.get("/v1/integrations", headers=headers)).json()
    providers = {row["provider"]: row for row in hub["providers"]}
    assert providers["MESSENGER"]["blocker"] == "META_APP_SETUP_REQUIRED"
    assert providers["SHOPIFY"]["available"] is False
    # Launch takes precedence even if an operator enables the billing switch.
    for enabled in (False, True):
        monkeypatch.setattr(settings, "billing_enabled", enabled)
        channel = (await client.get("/v1/billing/channel", headers=headers)).json()
        assert not channel["can_purchase"] and not channel["allows_external_payment_link"]
        assert channel["providers"] == []
        checkout = await client.post(
            "/v1/billing/web/checkout", headers=headers, json={"plan": "pro"}
        )
        assert checkout.status_code == 422


def test_billing_switch_is_independent_and_launch_wins(settings, monkeypatch):
    for launch, billing, expected in (
        (True, True, False),
        (True, False, False),
        (False, False, False),
        (False, True, True),
    ):
        monkeypatch.setattr(settings, "free_launch_mode", launch)
        monkeypatch.setattr(settings, "billing_enabled", billing)
        assert settings.purchases_enabled is expected


async def test_released_modules_remain_accessible_to_free_owner(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    for path in (
        "/v1/integrations",
        "/v1/automation/workflows",
        "/v1/campaigns",
        "/v1/forecasting/demand",
        "/v1/procurement/suppliers",
        "/v1/developers/keys",
        "/v1/network-intelligence",
        "/v1/team",
        "/v1/customers/risk-check?phone=01700000000",
    ):
        response = await client.get(path, headers=auth_header(shop))
        assert response.status_code == 200, (path, response.text)


async def test_checkout_service_cannot_be_called_around_the_ui(system_db, settings):
    from app.billing.models import BillingProviderKind
    from app.billing.providers.registry import build_registry
    from app.billing.service import BillingService
    from app.core.errors import ServiceUnavailableError

    service = BillingService(system_db, settings=settings, registry=build_registry(settings))
    with pytest.raises(ServiceUnavailableError):
        await service.start_checkout(
            tenant_id=uuid.uuid4(), plan=PlanCode.PRO, provider_kind=BillingProviderKind.BKASH_WEB
        )
