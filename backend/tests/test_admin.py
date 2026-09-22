"""Platform admin, RBAC and the boundaries between them.

Master spec sections 44, 88, 101, 102, 103. The tests are named after the things
that must be impossible:

*   a seller reaching the ops console;
*   an admin reading a secret, or a phone number, without asking for it;
*   an operator repairing another shop with the wrong role;
*   a repair running twice;
*   a shop being left with no Owner.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.models import (
    ADMIN_PERMISSIONS,
    AdminPermission,
    PlatformAdmin,
    PlatformAdminRole,
    RepairActionRecord,
    RepairOutcome,
    admin_permissions_for,
)
from app.admin.repair import REPAIR_ACTIONS
from app.common.audit import AuditAction, AuditLog
from app.common.provider_health import (
    BreakerState,
    HealthState,
    ProviderHealthService,
    ProviderKind,
)
from app.tenants.roles import Permission, TenantRole, has_permission, permissions_for
from tests.conftest_commerce import create_order, create_product, signed_in_shop
from tests.test_auth_flow import auth_header

BOOTSTRAP_TOKEN = "INSECURE_DEV_admin_bootstrap_token_for_tests_only_0123456789"


@pytest.fixture
def admin_client(settings: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Provision one bootstrap admin token for the running application.

    Set through the environment and the settings cache rather than by patching
    a dependency: ``get_app_settings`` is captured by FastAPI when the route is
    declared, so replacing the module attribute afterwards changes nothing.
    """
    from app.core.config import get_settings, reset_settings_cache

    monkeypatch.setenv("ADMIN_API_TOKENS", json.dumps([BOOTSTRAP_TOKEN]))
    reset_settings_cache()
    try:
        yield get_settings()
    finally:
        monkeypatch.undo()
        reset_settings_cache()


@pytest.fixture
def admin_settings(admin_client: Any) -> Any:
    """The settings the application is actually running with."""
    return admin_client


def admin_header(token: str = BOOTSTRAP_TOKEN) -> dict[str, str]:
    return {"X-Admin-Token": token}


async def _provision_admin(
    role: PlatformAdminRole, token: str, settings: Any, *, label: str | None = None
) -> uuid.UUID:
    """Create a real platform_admins row, the way an operator would."""
    from app.api.deps import get_hasher
    from app.db.session import system_session

    async with system_session("test fixture: provision admin") as session:
        row = PlatformAdmin(
            label=label or f"test-{role.lower()}",
            role=str(role),
            token_hash=get_hasher(settings).token_hash(token),
        )
        session.add(row)
        await session.flush()
        return row.id


# --------------------------------------------------------------------------- #
# RBAC matrix
# --------------------------------------------------------------------------- #


class TestPermissionMatrix:
    def test_the_spec_matrix_is_the_code(self) -> None:
        """Master spec section 88, asserted line by line."""
        assert has_permission(TenantRole.OWNER, Permission.BILLING_MANAGE)
        assert has_permission(TenantRole.OWNER, Permission.COURIER_CREDENTIAL_MANAGE)
        assert has_permission(TenantRole.OWNER, Permission.TEAM_MANAGE)

        # Manager: full operations, no money corrections, no credentials.
        assert has_permission(TenantRole.MANAGER, Permission.ORDER_CANCEL)
        assert has_permission(TenantRole.MANAGER, Permission.MONEY_VIEW)
        assert not has_permission(TenantRole.MANAGER, Permission.MONEY_CORRECT)
        assert not has_permission(TenantRole.MANAGER, Permission.COURIER_CREDENTIAL_MANAGE)
        assert not has_permission(TenantRole.MANAGER, Permission.BILLING_MANAGE)

        # Packer: pack and book. No financial visibility at all.
        assert has_permission(TenantRole.PACKER, Permission.ORDER_BOOK)
        assert not has_permission(TenantRole.PACKER, Permission.MONEY_VIEW)
        assert not has_permission(TenantRole.PACKER, Permission.ORDER_CANCEL)

        # Accountant: money, not merchandising.
        assert has_permission(TenantRole.ACCOUNTANT, Permission.MONEY_RECONCILE)
        assert has_permission(TenantRole.ACCOUNTANT, Permission.DATA_EXPORT)
        assert not has_permission(TenantRole.ACCOUNTANT, Permission.ORDER_WRITE)
        assert not has_permission(TenantRole.ACCOUNTANT, Permission.MONEY_CORRECT)

        # Viewer reads and nothing else.
        assert permissions_for(TenantRole.VIEWER) == frozenset(
            {Permission.ORDER_VIEW, Permission.CUSTOMER_VIEW, Permission.PRODUCT_VIEW}
        )

    def test_only_the_owner_holds_the_dangerous_permissions(self) -> None:
        dangerous = {
            Permission.BILLING_MANAGE,
            Permission.TEAM_MANAGE,
            Permission.SETTINGS_MANAGE,
            Permission.COURIER_CREDENTIAL_MANAGE,
            Permission.MONEY_CORRECT,
            Permission.CUSTOMER_EXPORT,
        }
        for role in TenantRole:
            if role is TenantRole.OWNER:
                continue
            assert not (permissions_for(role) & dangerous), role

    def test_an_unknown_role_grants_nothing(self) -> None:
        """A rolled-back release or a hand-edited row must not open a door."""
        assert permissions_for("SUPERUSER") == frozenset()


class TestAdminPermissionMatrix:
    def test_support_cannot_repair_reveal_or_grant(self) -> None:
        support = admin_permissions_for(PlatformAdminRole.SUPPORT)
        assert AdminPermission.TENANT_READ in support
        assert AdminPermission.REPAIR_RUN not in support
        assert AdminPermission.PII_REVEAL not in support
        assert AdminPermission.BILLING_GRANT not in support

    def test_ops_can_repair_but_cannot_reveal_or_grant(self) -> None:
        ops = admin_permissions_for(PlatformAdminRole.OPS)
        assert AdminPermission.REPAIR_RUN in ops
        assert AdminPermission.FEATURE_FLAG_WRITE in ops
        assert AdminPermission.PII_REVEAL not in ops
        assert AdminPermission.BILLING_GRANT not in ops

    def test_superadmin_holds_everything(self) -> None:
        assert ADMIN_PERMISSIONS[PlatformAdminRole.SUPERADMIN] == frozenset(AdminPermission)


# --------------------------------------------------------------------------- #
# The seller/admin boundary
# --------------------------------------------------------------------------- #


class TestAdminBoundary:
    async def test_admin_routes_reject_a_seller_token(
        self, client: AsyncClient, unique_phone: str, admin_client
    ) -> None:
        """The most important boundary in this module."""
        session = await signed_in_shop(client, unique_phone)
        for path in ("/v1/admin/me", "/v1/admin/tenants", "/v1/admin/ops/counts"):
            response = await client.get(path, headers=auth_header(session))
            assert response.status_code == 401, path

    async def test_admin_routes_reject_no_token(self, client: AsyncClient, admin_client) -> None:
        assert (await client.get("/v1/admin/me")).status_code == 401

    async def test_admin_routes_reject_a_wrong_token(
        self, client: AsyncClient, admin_client
    ) -> None:
        response = await client.get(
            "/v1/admin/me", headers={"X-Admin-Token": "not-the-configured-token"}
        )
        assert response.status_code == 401

    async def test_an_admin_token_grants_nothing_on_seller_routes(
        self, client: AsyncClient, admin_client
    ) -> None:
        """The boundary in the other direction."""
        response = await client.get("/v1/orders", headers=admin_header())
        assert response.status_code == 401

    async def test_with_no_admin_token_configured_there_is_no_console(
        self, client: AsyncClient
    ) -> None:
        """The shipped default. An ops console nobody provisioned is closed."""
        response = await client.get("/v1/admin/me", headers=admin_header())
        assert response.status_code == 401

    async def test_the_bootstrap_identity_says_it_is_a_bootstrap(
        self, client: AsyncClient, admin_client
    ) -> None:
        body = (await client.get("/v1/admin/me", headers=admin_header())).json()
        assert body["is_bootstrap"] is True
        assert body["role"] == "SUPERADMIN"
        assert "admin.pii_reveal" in body["permissions"]

    async def test_a_provisioned_support_admin_is_not_a_superadmin(
        self, client: AsyncClient, admin_client, admin_settings
    ) -> None:
        token = "INSECURE_DEV_support_token_for_tests_only_abcdefghijklmnop"
        await _provision_admin(PlatformAdminRole.SUPPORT, token, admin_settings)

        body = (await client.get("/v1/admin/me", headers=admin_header(token))).json()
        assert body["role"] == "SUPPORT"
        assert body["is_bootstrap"] is False
        assert "admin.repair_run" not in body["permissions"]

    async def test_a_revoked_admin_token_stops_working(
        self, client: AsyncClient, admin_client, admin_settings
    ) -> None:
        from app.core.clock import utc_now
        from app.db.session import system_session

        token = "INSECURE_DEV_revoked_token_for_tests_only_abcdefghijklmnop"
        admin_id = await _provision_admin(PlatformAdminRole.OPS, token, admin_settings)
        assert (await client.get("/v1/admin/me", headers=admin_header(token))).status_code == 200

        async with system_session("test fixture: revoke admin") as session:
            row = await session.get(PlatformAdmin, admin_id)
            assert row is not None
            row.revoked_at = utc_now()
            row.revoked_reason = "test"

        assert (await client.get("/v1/admin/me", headers=admin_header(token))).status_code == 401

    async def test_a_rejected_admin_token_is_audited(
        self, client: AsyncClient, admin_client, system_db: AsyncSession
    ) -> None:
        await client.get("/v1/admin/me", headers={"X-Admin-Token": "wrong-token-entirely"})
        entries = (
            (
                await system_db.execute(
                    sa.select(AuditLog).where(
                        AuditLog.action == str(AuditAction.ADMIN_ACCESS_DENIED)
                    )
                )
            )
            .scalars()
            .all()
        )
        assert entries, "a rejected admin token must leave a trace"


# --------------------------------------------------------------------------- #
# Console reads
# --------------------------------------------------------------------------- #


class TestAdminConsole:
    async def test_tenant_search_never_returns_a_dialable_number(
        self, client: AsyncClient, unique_phone: str, admin_client
    ) -> None:
        """Master spec section 101: masked by default."""
        await signed_in_shop(client, unique_phone, shop_name="Masked Shop")
        rows = (await client.get("/v1/admin/tenants?q=Masked", headers=admin_header())).json()
        assert rows
        masked = rows[0]["owner_masked_phone"]
        assert masked is not None
        assert unique_phone not in masked
        assert "*" in masked

    async def test_tenant_detail_masks_every_member(
        self, client: AsyncClient, unique_phone: str, admin_client
    ) -> None:
        session = await signed_in_shop(client, unique_phone, shop_name="Detail Shop")
        body = (
            await client.get(f"/v1/admin/tenants/{session['tenant_id']}", headers=admin_header())
        ).json()
        assert body["members"]
        for member in body["members"]:
            assert unique_phone not in member["masked_phone"]

    async def test_tenant_detail_carries_no_secret(
        self, client: AsyncClient, unique_phone: str, admin_client
    ) -> None:
        session = await signed_in_shop(client, unique_phone, shop_name="Secret Shop")
        raw = (
            await client.get(f"/v1/admin/tenants/{session['tenant_id']}", headers=admin_header())
        ).text.lower()
        for forbidden in (
            "phone_enc",
            "token_hash",
            "app_secret",
            "service_account",
            "purchase_token",
        ):
            assert forbidden not in raw

    async def test_viewing_a_shop_is_audited(
        self, client: AsyncClient, unique_phone: str, admin_client, system_db: AsyncSession
    ) -> None:
        session = await signed_in_shop(client, unique_phone, shop_name="Audited Shop")
        await client.get(f"/v1/admin/tenants/{session['tenant_id']}", headers=admin_header())

        entries = (
            (
                await system_db.execute(
                    sa.select(AuditLog).where(
                        AuditLog.action == str(AuditAction.ADMIN_TENANT_VIEWED),
                        AuditLog.entity_id == str(session["tenant_id"]),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(entries) == 1
        assert entries[0].context["admin"].startswith("bootstrap")

    async def test_ops_counts_answer(self, client: AsyncClient, admin_client) -> None:
        body = (await client.get("/v1/admin/ops/counts", headers=admin_header())).json()
        for key in (
            "booking_unknown",
            "reconciliation_cases_open",
            "billing_webhook_failures",
            "outbox_dead_letters",
            "sync_conflicts",
        ):
            assert key in body

    async def test_the_flag_list_reports_resolved_values(
        self, client: AsyncClient, admin_client
    ) -> None:
        rows = (await client.get("/v1/admin/flags", headers=admin_header())).json()
        by_key = {row["key"]: row for row in rows}
        assert by_key["steadfast_enabled"]["enabled"] is False
        assert by_key["play_billing_enabled"]["gates_billing_provider"] is True

    async def test_a_support_admin_cannot_read_the_audit_trail(
        self, client: AsyncClient, admin_client, admin_settings
    ) -> None:
        token = "INSECURE_DEV_support_audit_token_tests_only_abcdefghijklmn"
        await _provision_admin(PlatformAdminRole.SUPPORT, token, admin_settings)
        response = await client.get("/v1/admin/ops/audit", headers=admin_header(token))
        assert response.status_code == 403


# --------------------------------------------------------------------------- #
# PII reveal (master spec section 101)
# --------------------------------------------------------------------------- #


class TestPiiReveal:
    async def _customer(self, client: AsyncClient, session: dict[str, Any]) -> dict[str, Any]:
        response = await client.post(
            "/v1/customers",
            json={"phone": "01799001122", "name": "Rina"},
            headers=auth_header(session),
        )
        assert response.status_code == 201, response.text
        return response.json()

    async def test_a_reveal_requires_a_real_reason(
        self, client: AsyncClient, unique_phone: str, admin_client
    ) -> None:
        session = await signed_in_shop(client, unique_phone, shop_name="Reveal Shop")
        customer = await self._customer(client, session)

        response = await client.post(
            f"/v1/admin/tenants/{session['tenant_id']}/customers/{customer['id']}/reveal-phone",
            json={"reason": "why"},
            headers=admin_header(),
        )
        assert response.status_code == 422

    async def test_a_reveal_returns_the_number_and_is_audited(
        self, client: AsyncClient, unique_phone: str, admin_client, system_db: AsyncSession
    ) -> None:
        session = await signed_in_shop(client, unique_phone, shop_name="Reveal Shop")
        customer = await self._customer(client, session)

        response = await client.post(
            f"/v1/admin/tenants/{session['tenant_id']}/customers/{customer['id']}/reveal-phone",
            json={"reason": "seller called about a disputed COD on this parcel"},
            headers=admin_header(),
        )
        assert response.status_code == 200, response.text
        assert response.json()["phone"].endswith("1122")
        assert response.json()["expires_in_seconds"] > 0

        entries = (
            (
                await system_db.execute(
                    sa.select(AuditLog).where(
                        AuditLog.action == str(AuditAction.ADMIN_PII_REVEALED),
                        AuditLog.entity_id == str(customer["id"]),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(entries) == 1
        assert entries[0].reason.startswith("seller called")

    async def test_an_ops_admin_cannot_reveal(
        self, client: AsyncClient, unique_phone: str, admin_client, admin_settings
    ) -> None:
        token = "INSECURE_DEV_ops_noreveal_token_tests_only_abcdefghijklmno"
        await _provision_admin(PlatformAdminRole.OPS, token, admin_settings)
        session = await signed_in_shop(client, unique_phone, shop_name="Reveal Shop")
        customer = await self._customer(client, session)

        response = await client.post(
            f"/v1/admin/tenants/{session['tenant_id']}/customers/{customer['id']}/reveal-phone",
            json={"reason": "a perfectly good reason for looking"},
            headers=admin_header(token),
        )
        assert response.status_code == 403

    async def test_a_reveal_cannot_cross_shops(self, client: AsyncClient, admin_client) -> None:
        """The customer id must belong to the tenant in the path."""
        first = await signed_in_shop(client, "01712220001", shop_name="Shop One")
        second = await signed_in_shop(client, "01712220002", shop_name="Shop Two")
        customer = await self._customer(client, first)

        response = await client.post(
            f"/v1/admin/tenants/{second['tenant_id']}/customers/{customer['id']}/reveal-phone",
            json={"reason": "checking whether the scoping actually holds"},
            headers=admin_header(),
        )
        assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Support cases and the diagnostic bundle
# --------------------------------------------------------------------------- #


class TestSupportCases:
    async def test_a_case_gets_a_quotable_reference(
        self, client: AsyncClient, unique_phone: str, admin_client
    ) -> None:
        session = await signed_in_shop(client, unique_phone, shop_name="Case Shop")
        response = await client.post(
            "/v1/admin/support-cases",
            json={
                "subject": "COD not settled for three parcels",
                "case_type": "RECONCILIATION",
                "severity": "HIGH",
                "tenant_id": str(session["tenant_id"]),
            },
            headers=admin_header(),
        )
        assert response.status_code == 201, response.text
        assert response.json()["reference"].startswith("SC-")

    async def test_closing_a_case_needs_a_resolution(
        self, client: AsyncClient, admin_client
    ) -> None:
        created = (
            await client.post(
                "/v1/admin/support-cases",
                json={"subject": "Something went wrong", "case_type": "OTHER"},
                headers=admin_header(),
            )
        ).json()

        refused = await client.patch(
            f"/v1/admin/support-cases/{created['id']}",
            json={"status": "RESOLVED"},
            headers=admin_header(),
        )
        assert refused.status_code == 422

        accepted = await client.patch(
            f"/v1/admin/support-cases/{created['id']}",
            json={"status": "RESOLVED", "resolution": "Provider re-sent the statement."},
            headers=admin_header(),
        )
        assert accepted.status_code == 200
        assert accepted.json()["resolved_at"] is not None

    async def test_the_support_bundle_is_redacted(
        self, client: AsyncClient, unique_phone: str, admin_client
    ) -> None:
        """Section 102: ids and a timeline, never a credential or a number."""
        session = await signed_in_shop(client, unique_phone, shop_name="Bundle Shop")
        await create_product(client, session, name="Saree", sku="SAR-1")
        await create_order(client, session, phone="01798887766")

        raw = (
            await client.get(
                f"/v1/admin/tenants/{session['tenant_id']}/support-bundle",
                headers=admin_header(),
            )
        ).text
        assert "01798887766" not in raw
        assert unique_phone not in raw
        body = (
            await client.get(
                f"/v1/admin/tenants/{session['tenant_id']}/support-bundle",
                headers=admin_header(),
            )
        ).json()
        assert body["recent_orders"]
        assert "counts" in body and "provider_health" in body


# --------------------------------------------------------------------------- #
# Repairs (master spec section 103)
# --------------------------------------------------------------------------- #


class TestRepairs:
    async def test_every_repair_is_a_named_action(self, client: AsyncClient, admin_client) -> None:
        """There is no generic 'edit row' capability."""
        rows = (await client.get("/v1/admin/repairs", headers=admin_header())).json()
        names = {row["name"] for row in rows}
        assert names == set(REPAIR_ACTIONS)
        assert all(row["permitted"] for row in rows), "bootstrap is SUPERADMIN"

    async def test_a_repair_needs_a_reason(self, client: AsyncClient, admin_client) -> None:
        response = await client.post(
            "/v1/admin/repairs/refresh_provider_health",
            json={"reason": "why", "params": {"provider": "steadfast"}},
            headers=admin_header(),
        )
        assert response.status_code == 422

    async def test_an_unknown_repair_is_a_404_listing_the_real_ones(
        self, client: AsyncClient, admin_client
    ) -> None:
        response = await client.post(
            "/v1/admin/repairs/drop_all_tables",
            json={"reason": "a thoroughly stated reason"},
            headers=admin_header(),
        )
        assert response.status_code == 404
        assert "grant_support_credit" in response.json()["details"]["known"]

    async def test_a_repair_is_recorded_and_audited(
        self, client: AsyncClient, admin_client, system_db: AsyncSession
    ) -> None:
        response = await client.post(
            "/v1/admin/repairs/refresh_provider_health",
            json={
                "reason": "steadfast breaker opened during their maintenance window",
                "params": {"provider": "steadfast"},
                "idempotency_key": "test-refresh-1",
            },
            headers=admin_header(),
        )
        assert response.status_code == 200, response.text
        assert response.json()["outcome"] == "SUCCEEDED"

        records = (
            (
                await system_db.execute(
                    sa.select(RepairActionRecord).where(
                        RepairActionRecord.idempotency_key == "test-refresh-1"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(records) == 1
        assert records[0].reason.startswith("steadfast breaker")

        entries = (
            (
                await system_db.execute(
                    sa.select(AuditLog).where(
                        AuditLog.action == str(AuditAction.ADMIN_REPAIR_ACTION)
                    )
                )
            )
            .scalars()
            .all()
        )
        assert entries

    async def test_the_same_repair_twice_runs_once(self, client: AsyncClient, admin_client) -> None:
        """Two operators reacting to one alert must not both reverse a match."""
        payload = {
            "reason": "double-click protection check",
            "params": {"provider": "pathao"},
            "idempotency_key": "test-double-run",
        }
        first = await client.post(
            "/v1/admin/repairs/refresh_provider_health", json=payload, headers=admin_header()
        )
        second = await client.post(
            "/v1/admin/repairs/refresh_provider_health", json=payload, headers=admin_header()
        )
        assert first.json()["outcome"] == "SUCCEEDED"
        assert second.json()["outcome"] == str(RepairOutcome.REPLAYED)
        assert first.json()["id"] == second.json()["id"]

    async def test_support_cannot_run_a_repair(
        self, client: AsyncClient, admin_client, admin_settings
    ) -> None:
        token = "INSECURE_DEV_support_repair_token_tests_only_abcdefghijkl"
        await _provision_admin(PlatformAdminRole.SUPPORT, token, admin_settings)
        response = await client.post(
            "/v1/admin/repairs/refresh_provider_health",
            json={"reason": "trying something I should not be able to"},
            headers=admin_header(token),
        )
        assert response.status_code == 403

    async def test_ops_cannot_grant_support_credit(
        self, client: AsyncClient, unique_phone: str, admin_client, admin_settings
    ) -> None:
        """The one repair that gives away revenue needs its own permission."""
        token = "INSECURE_DEV_ops_credit_token_tests_only_abcdefghijklmnop"
        await _provision_admin(PlatformAdminRole.OPS, token, admin_settings)
        session = await signed_in_shop(client, unique_phone, shop_name="Credit Shop")

        response = await client.post(
            "/v1/admin/repairs/grant_support_credit",
            json={
                "reason": "apologising for the payout import bug",
                "tenant_id": str(session["tenant_id"]),
                "params": {"plan": "pro", "days": 30},
            },
            headers=admin_header(token),
        )
        assert response.status_code == 403

    async def test_a_superadmin_can_grant_support_credit(
        self, client: AsyncClient, unique_phone: str, admin_client
    ) -> None:
        session = await signed_in_shop(client, unique_phone, shop_name="Credit Shop")
        response = await client.post(
            "/v1/admin/repairs/grant_support_credit",
            json={
                "reason": "apologising for the payout import bug",
                "tenant_id": str(session["tenant_id"]),
                "params": {"plan": "pro", "days": 30},
            },
            headers=admin_header(),
        )
        assert response.status_code == 200, response.text
        assert response.json()["outcome"] == "SUCCEEDED"

        entitlements = (
            await client.get("/v1/billing/entitlements", headers=auth_header(session))
        ).json()
        assert entitlements["plan"] == "pro"

    async def test_revoking_a_courier_credential_reports_it_cannot(
        self, client: AsyncClient, unique_phone: str, admin_client
    ) -> None:
        """Better an honest UNAVAILABLE than a silent no-op on a leaked key."""
        session = await signed_in_shop(client, unique_phone, shop_name="Courier Shop")
        response = await client.post(
            "/v1/admin/repairs/revoke_courier_credential",
            json={
                "reason": "seller reported their API key in a screenshot",
                "tenant_id": str(session["tenant_id"]),
            },
            headers=admin_header(),
        )
        assert response.json()["outcome"] == str(RepairOutcome.UNAVAILABLE)

    async def test_the_money_summary_repair_checks_the_ledger_invariant(
        self, client: AsyncClient, unique_phone: str, admin_client
    ) -> None:
        session = await signed_in_shop(client, unique_phone, shop_name="Money Shop")
        response = await client.post(
            "/v1/admin/repairs/rebuild_money_summary",
            json={
                "reason": "seller says the outstanding figure looks wrong",
                "tenant_id": str(session["tenant_id"]),
            },
            headers=admin_header(),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["outcome"] == "SUCCEEDED"
        assert body["data"]["difference_paisa"] == 0

    async def test_a_flag_repair_disables_a_provider(
        self, client: AsyncClient, admin_client
    ) -> None:
        """Section 44: survive a provider outage without shipping an app."""
        response = await client.post(
            "/v1/admin/repairs/disable_provider_capability",
            json={
                "reason": "pathao returning 500s across the board",
                "params": {"flag": "pathao_enabled", "enabled": False},
            },
            headers=admin_header(),
        )
        assert response.status_code == 200, response.text
        assert response.json()["outcome"] == "SUCCEEDED"

    async def test_an_unknown_flag_is_refused_with_the_known_list(
        self, client: AsyncClient, admin_client
    ) -> None:
        response = await client.post(
            "/v1/admin/repairs/disable_provider_capability",
            json={
                "reason": "typing a flag that does not exist",
                "params": {"flag": "make_everything_free", "enabled": True},
            },
            headers=admin_header(),
        )
        assert response.status_code == 422
        assert "steadfast_enabled" in response.json()["details"]["known"]


# --------------------------------------------------------------------------- #
# Provider health
# --------------------------------------------------------------------------- #


class TestProviderHealth:
    async def test_one_failure_does_not_take_a_provider_down(self, system_db: AsyncSession) -> None:
        """The rule the Phase F brief states explicitly."""
        health = ProviderHealthService(system_db)
        await health.record_success(ProviderKind.COURIER, "steadfast")
        row = await health.record_failure(ProviderKind.COURIER, "steadfast", error_code="TIMEOUT")
        assert row.state != str(HealthState.DOWN)
        assert row.breaker_state == str(BreakerState.CLOSED)

    async def test_a_streak_of_failures_opens_the_breaker(self, system_db: AsyncSession) -> None:
        health = ProviderHealthService(system_db)
        for _ in range(5):
            row = await health.record_failure(ProviderKind.COURIER, "redx", error_code="HTTP_500")
        assert row.state == str(HealthState.DOWN)
        assert row.breaker_state == str(BreakerState.OPEN)
        assert await health.allows("redx") is False

    async def test_an_auth_failure_asks_for_a_reconnect_immediately(
        self, system_db: AsyncSession
    ) -> None:
        """It will not fix itself, so waiting is the wrong instruction."""
        health = ProviderHealthService(system_db)
        row = await health.record_failure(
            ProviderKind.COURIER, "pathao", error_code="401", is_auth_failure=True
        )
        assert row.state == str(HealthState.NEEDS_RECONNECT)

    async def test_a_success_closes_the_breaker(self, system_db: AsyncSession) -> None:
        health = ProviderHealthService(system_db)
        for _ in range(5):
            await health.record_failure(ProviderKind.SMS, "gateway", error_code="X")
        row = await health.record_success(ProviderKind.SMS, "gateway", latency_ms=120)
        assert row.state == str(HealthState.HEALTHY)
        assert row.breaker_state == str(BreakerState.CLOSED)
        assert row.latency_ms_ema == 120

    async def test_health_is_per_tenant_where_it_should_be(self, system_db: AsyncSession) -> None:
        """One seller's bad key must not disable the provider for everyone."""
        health = ProviderHealthService(system_db)
        tenant_id = uuid.uuid4()
        await health.record_failure(
            ProviderKind.COURIER,
            "steadfast",
            tenant_id=tenant_id,
            error_code="401",
            is_auth_failure=True,
        )
        assert await health.allows("steadfast", tenant_id=tenant_id) is False
        assert await health.allows("steadfast") is True

    async def test_a_state_change_is_audited(self, system_db: AsyncSession) -> None:
        health = ProviderHealthService(system_db)
        await health.record_success(ProviderKind.BILLING, "play")
        await system_db.flush()
        entries = (
            (
                await system_db.execute(
                    sa.select(AuditLog).where(
                        AuditLog.action == str(AuditAction.PROVIDER_HEALTH_CHANGED)
                    )
                )
            )
            .scalars()
            .all()
        )
        assert entries
