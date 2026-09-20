"""Team management and role enforcement over HTTP.

Master spec sections 88 and 89. The permission matrix is only real if a
non-Owner actually hits it, so these tests create real members with real roles
and drive the API as them.
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest_commerce import create_product, grant_plan, signed_in_shop
from tests.test_auth_flow import auth_header, sign_in

from app.entitlements.catalog import Entitlement
from app.tenants.roles import TenantRole


async def _member_session(
    client: AsyncClient, owner: dict[str, Any], phone: str, role: TenantRole
) -> dict[str, Any]:
    """Invite someone to the owner's shop, have them accept, and sign them in.

    V2.1 made membership an offer: the owner sends an invitation and the person
    joins by accepting it with the number it was addressed to. This helper runs
    that whole flow, so a test that needs a member gets one the way a real one
    arrives — rather than a membership conjured directly into the table.
    """
    invited = await client.post(
        "/v1/team/invitations",
        json={"phone": phone, "role": str(role)},
        headers=auth_header(owner),
    )
    assert invited.status_code == 201, invited.text

    session = await sign_in(client, phone)

    # The invitee accepts with their own session. Only the number the
    # invitation was addressed to can do this.
    mine = await client.get("/v1/team/invitations/mine", headers=auth_header(session))
    assert mine.status_code == 200, mine.text
    pending = mine.json()
    assert len(pending) == 1, pending
    accepted = await client.post(
        f"/v1/team/invitations/{pending[0]['invitation_id']}/accept",
        headers=auth_header(session),
    )
    assert accepted.status_code == 200, accepted.text

    # Re-read: the session was bound before the membership existed.
    session = await sign_in(client, phone)
    if session["tenant_id"] is None:
        selected = await client.post(
            "/v1/auth/select-tenant",
            json={"tenant_id": owner["tenant_id"]},
            headers=auth_header(session),
        )
        assert selected.status_code == 200, selected.text
        # Shop selection returns metadata, not replacement credentials.
        session.update(selected.json())
    return session


class TestTeamRoster:
    async def test_the_owner_is_listed_with_their_permissions(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        owner = await signed_in_shop(client, unique_phone, shop_name="Roster Shop", plan="pro")
        rows = (await client.get("/v1/team", headers=auth_header(owner))).json()
        assert len(rows) == 1
        assert rows[0]["role"] == "OWNER"
        assert rows[0]["is_self"] is True
        assert "billing.manage" in rows[0]["permissions"]

    async def test_the_roster_never_lists_a_dialable_number(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        owner = await signed_in_shop(client, unique_phone, shop_name="Roster Shop", plan="pro")
        rows = (await client.get("/v1/team", headers=auth_header(owner))).json()
        assert unique_phone not in rows[0]["masked_phone"]

    async def test_the_matrix_endpoint_matches_the_code(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        owner = await signed_in_shop(client, unique_phone, shop_name="Roster Shop", plan="pro")
        matrix = (await client.get("/v1/team/roles", headers=auth_header(owner))).json()
        assert set(matrix) == {str(role) for role in TenantRole}
        assert "money.reconcile" in matrix["FINANCE"]
        assert "money.reconcile" not in matrix["ORDER_OPERATOR"]


class TestTeamMembership:
    async def test_a_member_can_be_added_and_signs_in_to_the_shop(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        owner = await signed_in_shop(client, unique_phone, shop_name="Team Shop", plan="pro")
        member = await _member_session(client, owner, "01733111001", TenantRole.PACKER)
        assert member["tenant_id"] == owner["tenant_id"]
        assert member["role"] == "ORDER_OPERATOR"

    async def test_adding_the_same_number_twice_is_a_conflict(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        owner = await signed_in_shop(client, unique_phone, shop_name="Team Shop", plan="pro")
        payload = {"phone": "01733111002", "role": "MANAGER"}
        assert (
            await client.post("/v1/team/invitations", json=payload, headers=auth_header(owner))
        ).status_code == 201
        second = await client.post("/v1/team/invitations", json=payload, headers=auth_header(owner))
        assert second.status_code == 409

    async def test_an_invalid_number_is_refused(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        owner = await signed_in_shop(client, unique_phone, shop_name="Team Shop", plan="pro")
        response = await client.post(
            "/v1/team/invitations",
            json={"phone": "12345", "role": "VIEWER"},
            headers=auth_header(owner),
        )
        assert response.status_code == 422

    async def test_the_seat_limit_is_a_standing_cap(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        """Starter includes one seat: the Owner. There is no second."""
        owner = await signed_in_shop(client, unique_phone, shop_name="Solo Shop", plan="starter")
        response = await client.post(
            "/v1/team/invitations",
            json={"phone": "01733111003", "role": "ORDER_OPERATOR"},
            headers=auth_header(owner),
        )
        assert response.status_code == 402
        body = response.json()
        assert body["code"] == "ENTITLEMENT_REQUIRED"
        assert body["details"]["entitlement"] == str(Entitlement.TEAM_MEMBER_LIMIT)

    async def test_removing_a_member_frees_their_seat(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        """A standing cap, not a period quota: leaving gives the seat back."""
        owner = await signed_in_shop(client, unique_phone, shop_name="Pro Shop", plan="pro")
        # Real members, accepted, because the seat is freed by removing one.
        for index in range(4):
            await _member_session(client, owner, f"0173322100{index}", TenantRole.ORDER_OPERATOR)

        full = await client.post(
            "/v1/team/invitations",
            json={"phone": "01733222999", "role": "ORDER_OPERATOR"},
            headers=auth_header(owner),
        )
        assert full.status_code == 402

        roster = (await client.get("/v1/team", headers=auth_header(owner))).json()
        victim = next(row for row in roster if row["role"] == "ORDER_OPERATOR")
        removed = await client.delete(f"/v1/team/{victim['user_id']}", headers=auth_header(owner))
        assert removed.status_code == 204

        again = await client.post(
            "/v1/team/invitations",
            json={"phone": "01733222999", "role": "ORDER_OPERATOR"},
            headers=auth_header(owner),
        )
        assert again.status_code == 201

    async def test_a_shop_cannot_be_left_without_an_owner(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        owner = await signed_in_shop(client, unique_phone, shop_name="Solo Owner", plan="pro")
        me = (await client.get("/v1/me", headers=auth_header(owner))).json()

        demoted = await client.patch(
            f"/v1/team/{me['user_id']}",
            json={"role": "MANAGER"},
            headers=auth_header(owner),
        )
        assert demoted.status_code == 409

        removed = await client.delete(f"/v1/team/{me['user_id']}", headers=auth_header(owner))
        assert removed.status_code == 409

    async def test_removing_a_member_revokes_their_sessions_immediately(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        """Access ends now, not at token expiry."""
        owner = await signed_in_shop(client, unique_phone, shop_name="Revoke Shop", plan="pro")
        member = await _member_session(client, owner, "01733111004", TenantRole.MANAGER)
        assert (await client.get("/v1/orders", headers=auth_header(member))).status_code == 200

        await client.delete(f"/v1/team/{member['user_id']}", headers=auth_header(owner))
        after = await client.get("/v1/orders", headers=auth_header(member))
        assert after.status_code == 401
        assert after.json()["code"] == "SESSION_REVOKED"

    async def test_re_adding_someone_reuses_their_membership_row(
        self, client: AsyncClient, unique_phone: str, system_db: AsyncSession
    ) -> None:
        """Their history in this shop stays attached to the same row."""
        from app.tenants.models import TenantUser

        owner = await signed_in_shop(client, unique_phone, shop_name="Rejoin Shop", plan="pro")
        member = await _member_session(client, owner, "01733111005", TenantRole.PACKER)
        await client.delete(f"/v1/team/{member['user_id']}", headers=auth_header(owner))

        rejoined = await _member_session(client, owner, "01733111005", TenantRole.FINANCE)
        assert rejoined["role"] == "FINANCE"

        rows = (
            (
                await system_db.execute(
                    sa.select(TenantUser).where(
                        TenantUser.tenant_id == uuid.UUID(owner["tenant_id"]),
                        TenantUser.user_id == uuid.UUID(member["user_id"]),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1


class TestRoleEnforcement:
    async def test_a_packer_cannot_see_money(self, client: AsyncClient, unique_phone: str) -> None:
        owner = await signed_in_shop(client, unique_phone, shop_name="Enforce Shop", plan="pro")
        packer = await _member_session(client, owner, "01733444001", TenantRole.PACKER)

        assert (await client.get("/v1/orders", headers=auth_header(packer))).status_code == 200
        assert (await client.get("/v1/team", headers=auth_header(packer))).status_code == 403

    async def test_a_manager_cannot_manage_billing(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        owner = await signed_in_shop(client, unique_phone, shop_name="Enforce Shop", plan="pro")
        manager = await _member_session(client, owner, "01733444002", TenantRole.MANAGER)

        response = await client.post("/v1/billing/cancel", json={}, headers=auth_header(manager))
        assert response.status_code == 403

    async def test_a_manager_cannot_add_team_members(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        owner = await signed_in_shop(client, unique_phone, shop_name="Enforce Shop", plan="pro")
        manager = await _member_session(client, owner, "01733444003", TenantRole.MANAGER)

        response = await client.post(
            "/v1/team/invitations",
            json={"phone": "01733444099", "role": "VIEWER"},
            headers=auth_header(manager),
        )
        assert response.status_code == 403

    async def test_a_viewer_cannot_create_a_product(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        owner = await signed_in_shop(client, unique_phone, shop_name="Enforce Shop", plan="pro")
        await create_product(client, owner, name="Kurti", sku="ENF-1")
        viewer = await _member_session(client, owner, "01733444004", TenantRole.VIEWER)

        listed = await client.get("/v1/products", headers=auth_header(viewer))
        assert listed.status_code == 200
        assert listed.json()["items"]

        created = await client.post(
            "/v1/products",
            json={
                "name": "Sneaky",
                "cost_paisa": 100,
                "default_selling_price_paisa": 200,
                "opening_stock": 1,
            },
            headers=auth_header(viewer),
        )
        assert created.status_code in (401, 403)

    async def test_a_member_of_one_shop_cannot_reach_another(self, client: AsyncClient) -> None:
        """The team feature must not become a cross-tenant hole."""
        first = await signed_in_shop(client, "01733555001", shop_name="Shop One", plan="pro")
        second = await signed_in_shop(client, "01733555002", shop_name="Shop Two", plan="pro")
        await grant_plan(str(second["tenant_id"]), "pro")

        member = await _member_session(client, first, "01733555003", TenantRole.MANAGER)
        response = await client.post(
            "/v1/auth/select-tenant",
            json={"tenant_id": second["tenant_id"]},
            headers=auth_header(member),
        )
        assert response.status_code in (403, 404)
