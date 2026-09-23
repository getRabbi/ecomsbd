"""Shop onboarding and the tenant-scoped API surface.

Master spec sections 4 and 55: a new seller can create an account and a shop,
and can skip courier linking entirely.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from tests.test_auth_flow import auth_header, sign_in


async def create_shop(
    client: AsyncClient, session: dict[str, Any], **overrides: Any
) -> dict[str, Any]:
    payload = {
        "name": "Noor Fashion",
        "business_category": "CLOTHING",
        "pickup_contact_name": "Noor",
        "pickup_phone": "01712345678",
        "pickup_address": "House 12, Road 3, Mirpur 10, Dhaka",
        "pickup_district": "Dhaka",
        "pickup_area": "Mirpur",
    }
    payload.update(overrides)
    response = await client.post("/v1/tenants", json=payload, headers=auth_header(session))
    assert response.status_code == 201, response.text
    # The shop is bound to the caller's session on the server and no new
    # credentials are issued: the token the seller already holds is the one
    # that now resolves to this shop.
    return {**response.json(), "access_token": session["access_token"]}


class TestShopCreation:
    async def test_creating_a_shop_binds_the_session_to_it(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await sign_in(client, unique_phone)
        assert session["tenant_id"] is None

        response = await client.post(
            "/v1/tenants", json={"name": "Noor Fashion"}, headers=auth_header(session)
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["tenant_id"] is not None
        assert body["role"] == "OWNER"
        # No token is reissued. The binding lives on the server-side session.
        assert "access_token" not in body
        assert "refresh_token" not in body

        # The very next request, with the credentials the seller already had,
        # sees the new shop.
        shop = await client.get("/v1/tenant", headers=auth_header(session))
        assert shop.status_code == 200, shop.text
        assert shop.json()["id"] == body["tenant_id"]
        me = await client.get("/v1/me", headers=auth_header(session))
        assert me.json()["needs_onboarding"] is True

    async def test_the_new_shop_is_readable(self, client: AsyncClient, unique_phone: str) -> None:
        session = await create_shop(client, await sign_in(client, unique_phone))
        response = await client.get("/v1/tenant", headers=auth_header(session))
        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Noor Fashion"
        assert body["timezone"] == "Asia/Dhaka"
        assert body["currency"] == "BDT"

    async def test_pickup_phone_is_not_returned_to_the_client(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await create_shop(client, await sign_in(client, unique_phone))
        response = await client.get("/v1/tenant", headers=auth_header(session))
        assert "01712345678" not in response.text

    async def test_raw_address_is_preserved_verbatim(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        # Master spec section 71: the seller's own text is evidence and is
        # never overwritten by a normalized or courier-transformed version.
        address = "  House 12, Road 3, Mirpur 10, Dhaka  "
        session = await create_shop(
            client, await sign_in(client, unique_phone), pickup_address=address
        )
        response = await client.get("/v1/tenant", headers=auth_header(session))
        assert response.json()["pickup_address_raw"] == address

    async def test_a_shop_can_be_created_without_courier_details(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await sign_in(client, unique_phone)
        response = await client.post(
            "/v1/tenants", json={"name": "Small Shop"}, headers=auth_header(session)
        )
        assert response.status_code == 201

    async def test_shop_name_is_validated(self, client: AsyncClient, unique_phone: str) -> None:
        session = await sign_in(client, unique_phone)
        response = await client.post(
            "/v1/tenants", json={"name": "x"}, headers=auth_header(session)
        )
        assert response.status_code == 422
        assert response.json()["code"] == "VALIDATION_ERROR"


class TestOnboardingProgress:
    async def test_progress_is_stored_server_side(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        # Kept on the server so a reinstall resumes rather than restarting.
        session = await create_shop(client, await sign_in(client, unique_phone))
        response = await client.patch(
            "/v1/tenant",
            json={"onboarding_step": "COMPLETE"},
            headers=auth_header(session),
        )
        assert response.status_code == 200
        assert response.json()["onboarding_step"] == "COMPLETE"
        assert response.json()["onboarding_completed_at"] is not None

    async def test_me_reflects_completed_onboarding(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await create_shop(client, await sign_in(client, unique_phone))
        assert (await client.get("/v1/me", headers=auth_header(session))).json()[
            "needs_onboarding"
        ] is True

        await client.patch(
            "/v1/tenant", json={"onboarding_step": "COMPLETE"}, headers=auth_header(session)
        )
        me = await client.get("/v1/me", headers=auth_header(session))
        assert me.json()["needs_onboarding"] is False


class TestTenantScopedEndpoints:
    async def test_tenant_endpoints_require_a_shop(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await sign_in(client, unique_phone)
        response = await client.get("/v1/tenant", headers=auth_header(session))
        assert response.status_code == 403
        assert response.json()["code"] == "FORBIDDEN"

    async def test_one_seller_cannot_read_anothers_shop(self, client: AsyncClient) -> None:
        # The section 47 requirement, exercised over HTTP rather than the ORM.
        first = await create_shop(client, await sign_in(client, "01711111111"), name="Shop One")
        second = await create_shop(client, await sign_in(client, "01822222222"), name="Shop Two")

        response = await client.get("/v1/tenant", headers=auth_header(second))
        assert response.json()["name"] == "Shop Two"
        assert response.json()["id"] != first["tenant_id"]

    async def test_selecting_an_unowned_shop_is_refused(self, client: AsyncClient) -> None:
        first = await create_shop(client, await sign_in(client, "01733333333"), name="Shop Three")
        outsider = await sign_in(client, "01844444444")

        response = await client.post(
            "/v1/auth/select-tenant",
            json={"tenant_id": first["tenant_id"]},
            headers=auth_header(outsider),
        )
        assert response.status_code == 403


class TestEntitlements:
    async def test_a_new_shop_starts_on_the_free_plan(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await create_shop(client, await sign_in(client, unique_phone))
        response = await client.get("/v1/billing/entitlements", headers=auth_header(session))
        assert response.status_code == 200
        body = response.json()
        assert body["plan"] == "free"
        assert body["entitlements"]["orders_monthly_limit"] == 20
        assert body["entitlements"]["reconciliation"] is False

    async def test_plan_catalogue_prices_are_paisa(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await create_shop(client, await sign_in(client, unique_phone))
        response = await client.get("/v1/billing/plans", headers=auth_header(session))
        plans = {plan["code"]: plan for plan in response.json()}
        assert plans["starter"]["price"]["amount_paisa"] == 19_900  # ৳199
        assert plans["pro"]["price"]["amount_paisa"] == 39_900  # ৳399
        assert plans["free"]["price"]["currency"] == "BDT"


class TestProviderCapabilities:
    async def test_providers_report_verified_capabilities_only(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await create_shop(client, await sign_in(client, unique_phone))
        response = await client.get("/v1/couriers/providers", headers=auth_header(session))
        assert response.status_code == 200
        providers = {p["provider"]: p for p in response.json()}

        # Steadfast's V1 documentation has been read, so its capabilities are
        # now claims with evidence behind them rather than blanket unknowns.
        steadfast = providers["steadfast"]
        assert steadfast["fully_unverified"] is False
        assert steadfast["documentation_version"] == "V1"
        capabilities = steadfast["capabilities"]

        # Exactly the endpoints the document describes.
        for capability in (
            "create_single",
            "create_bulk",
            "status_lookup",
            "balance",
            "returns",
            "payments",
            "payment_consignments",
        ):
            assert capabilities[capability] == "true", capability

        # The document contains no webhook section at all. The infrastructure
        # exists and is tested, but the capability must stay `unknown` until a
        # real contract is supplied — never `false`, which would claim we know
        # Steadfast has none, and never `true`.
        assert capabilities["webhook"] == "unknown"
        assert steadfast["unknowns"]["WEBHOOK_CONTRACT"] == "unknown"
        assert steadfast["unknowns"]["PROVIDER_CREATE_IDEMPOTENCY"] == "unknown"

        # Endpoints the document positively does not contain are `false`, so
        # the UI shows the manual alternative rather than a broken button.
        for capability in ("price_quote", "customer_stats", "cancel", "list_stores"):
            assert capabilities[capability] == "false", capability

        # Still behind its flag: documentation read is not the same as rolled
        # out to sellers, and the kill switch must exist before the feature.
        assert steadfast["enabled"] is False
        assert steadfast["manual_fallback"]

        # Pathao is implemented from its merchant API documentation, and like
        # Steadfast before rollout it stays behind its flag until live
        # merchant credentials and webhook registration have been verified.
        pathao = providers["pathao"]
        assert pathao["fully_unverified"] is False
        assert pathao["enabled"] is False

        # RedX is implemented from its own developer documentation, is
        # connectable, and stays behind its flag until a shop is given it.
        redx = providers["redx"]
        assert redx["fully_unverified"] is False
        assert redx["capabilities"]["create_single"] == "true"
        assert redx["capabilities"]["create_bulk"] == "false"
        assert redx["connect_form"]["fields"][0]["name"] == "api_token"
        assert redx["enabled"] is False

        # Manual mode always works and is never gated.
        assert providers["manual"]["enabled"] is True
