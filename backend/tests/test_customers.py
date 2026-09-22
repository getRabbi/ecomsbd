"""Customer CRM.

Master spec sections 21, 101, 130 and 133. The properties under test:

*   the full phone number never leaves the server except through the audited
    reveal path;
*   lookup works from every shape a seller might type;
*   a customer belongs to one seller and is invisible to every other.
"""

from __future__ import annotations

import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest_commerce import signed_in_shop
from tests.test_auth_flow import auth_header


async def create_customer(
    client: AsyncClient, session: dict, *, phone: str, **extra: object
) -> dict:
    payload: dict = {"phone": phone}
    payload.update(extra)
    response = await client.post("/v1/customers", json=payload, headers=auth_header(session))
    assert response.status_code == 201, response.text
    return response.json()


class TestCustomerPrivacy:
    async def test_the_api_never_returns_a_full_phone_number(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        await create_customer(client, session, phone="01712345678", name="Nusrat")

        listed = await client.get("/v1/customers", headers=auth_header(session))
        assert "01712345678" not in listed.text
        assert "+8801712345678" not in listed.text
        assert listed.json()["items"][0]["phone_masked"] == "01712****78"

    async def test_the_stored_row_holds_no_clear_text_number(
        self, client: AsyncClient, unique_phone: str, system_db: AsyncSession
    ) -> None:
        # The database itself must not be a phone list (master spec section 133).
        session = await signed_in_shop(client, unique_phone)
        await create_customer(client, session, phone="01755443322")

        rows = (
            await system_db.execute(sa.text("SELECT phone_enc, phone_search_hmac FROM customers"))
        ).all()
        blob = " ".join(str(value) for row in rows for value in row)
        assert "01755443322" not in blob
        assert "+8801755443322" not in blob

    async def test_reveal_returns_the_number_and_is_audited(
        self, client: AsyncClient, unique_phone: str, system_db: AsyncSession
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        customer = await create_customer(client, session, phone="01799887766")

        response = await client.post(
            f"/v1/customers/{customer['id']}/reveal-phone",
            json={"reason": "Calling about a failed delivery"},
            headers=auth_header(session),
        )
        assert response.status_code == 200
        assert response.json()["phone"] == "+8801799887766"

        actions = set(
            (await system_db.execute(sa.text("SELECT action FROM audit_logs"))).scalars().all()
        )
        assert "privacy.phone_revealed" in actions

    async def test_reveal_requires_a_reason(self, client: AsyncClient, unique_phone: str) -> None:
        session = await signed_in_shop(client, unique_phone)
        customer = await create_customer(client, session, phone="01711223344")

        response = await client.post(
            f"/v1/customers/{customer['id']}/reveal-phone",
            json={"reason": ""},
            headers=auth_header(session),
        )
        assert response.status_code == 422


class TestCustomerLookup:
    async def test_every_input_shape_finds_the_same_record(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        created = await create_customer(client, session, phone="01712345678")

        for variant in (
            "01712345678",
            "+8801712345678",
            "8801712345678",
            "017-1234 5678",
            "০১৭১২৩৪৫৬৭৮",
        ):
            response = await client.get(
                f"/v1/customers/lookup?phone={variant}", headers=auth_header(session)
            )
            assert response.status_code == 200, variant
            assert response.json()["id"] == created["id"], variant

    async def test_an_unknown_number_returns_null_not_an_error(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        # A new customer is the normal case in the order flow, not a failure.
        session = await signed_in_shop(client, unique_phone)
        response = await client.get(
            "/v1/customers/lookup?phone=01999888777", headers=auth_header(session)
        )
        assert response.status_code == 200
        assert response.json() is None

    async def test_the_same_phone_twice_is_a_conflict(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        await create_customer(client, session, phone="01712345678")

        response = await client.post(
            "/v1/customers",
            json={"phone": "+8801712345678"},
            headers=auth_header(session),
        )
        assert response.status_code == 409

    async def test_search_by_name_and_by_last_four(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        await create_customer(client, session, phone="01712345678", name="Nusrat Jahan")
        await create_customer(client, session, phone="01819223344", name="Rafi Hasan")

        by_name = await client.get("/v1/customers?search=nusrat", headers=auth_header(session))
        assert [row["name"] for row in by_name.json()["items"]] == ["Nusrat Jahan"]

        by_digits = await client.get("/v1/customers?search=3344", headers=auth_header(session))
        assert [row["name"] for row in by_digits.json()["items"]] == ["Rafi Hasan"]


class TestCustomerMetrics:
    async def test_a_new_customer_has_no_success_rate(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        # Master spec sections 24 and 121: no ranking without a sample. Showing
        # 0% would read as a judgement the data does not support.
        session = await signed_in_shop(client, unique_phone)
        customer = await create_customer(client, session, phone="01712345678")

        assert customer["order_count"] == 0
        assert customer["success_rate_basis_points"] is None
        assert customer["is_repeat_buyer"] is False

    async def test_order_count_and_repeat_flag_track_orders(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)

        for _ in range(2):
            await client.post(
                "/v1/orders",
                json={
                    "phone": "01712345678",
                    "items": [{"name": "Abaya", "quantity": 1, "unit_price_paisa": 125_000}],
                },
                headers=auth_header(session),
            )

        listed = await client.get("/v1/customers", headers=auth_header(session))
        customer = listed.json()["items"][0]
        assert customer["order_count"] == 2
        assert customer["is_repeat_buyer"] is True
        assert customer["first_order_at"] is not None


class TestSellerPrivateFlags:
    async def test_star_and_block_are_seller_private(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        customer = await create_customer(client, session, phone="01712345678")

        response = await client.patch(
            f"/v1/customers/{customer['id']}",
            json={"flag": "BLOCKED", "flag_reason": "Refused three parcels"},
            headers=auth_header(session),
        )
        assert response.status_code == 200
        assert response.json()["flag"] == "BLOCKED"

    async def test_a_block_does_not_follow_the_customer_to_another_shop(
        self, client: AsyncClient
    ) -> None:
        # Master spec section 130: no shared blacklist across sellers, ever.
        first = await signed_in_shop(client, "01744400011", shop_name="Shop A")
        second = await signed_in_shop(client, "01744400022", shop_name="Shop B")

        blocked = await create_customer(client, first, phone="01712345678")
        await client.patch(
            f"/v1/customers/{blocked['id']}",
            json={"flag": "BLOCKED", "flag_reason": "private"},
            headers=auth_header(first),
        )

        # The same person in the second shop is a fresh, unflagged record.
        theirs = await create_customer(client, second, phone="01712345678")
        assert theirs["flag"] == "NONE"
        assert theirs["id"] != blocked["id"]

    async def test_blocked_customers_cannot_be_ordered_for(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        customer = await create_customer(client, session, phone="01712345678")
        await client.patch(
            f"/v1/customers/{customer['id']}",
            json={"flag": "BLOCKED", "flag_reason": "private"},
            headers=auth_header(session),
        )

        response = await client.post(
            "/v1/orders",
            json={
                "phone": "01712345678",
                "items": [{"name": "Abaya", "quantity": 1, "unit_price_paisa": 125_000}],
            },
            headers=auth_header(session),
        )
        # A conflict the seller can lift by unblocking, not a silent refusal.
        assert response.status_code == 409


class TestCustomerAddresses:
    async def test_the_raw_address_is_kept_verbatim(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        # Master spec section 71: the seller's own text is evidence.
        session = await signed_in_shop(client, unique_phone)
        raw = "  House 12,   Road 3,  Mirpur 10  "
        customer = await create_customer(client, session, phone="01712345678", address=raw)

        address = customer["addresses"][0]
        assert address["raw_address"] == raw
        assert address["normalized_address"] == "House 12, Road 3, Mirpur 10"
        assert address["is_default"] is True

    async def test_an_identical_address_is_not_duplicated(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        customer = await create_customer(
            client, session, phone="01712345678", address="Mirpur 10, Dhaka"
        )

        for _ in range(2):
            await client.post(
                "/v1/orders",
                json={
                    "phone": "01712345678",
                    "address": "Mirpur 10,  Dhaka",
                    "items": [{"name": "Abaya", "quantity": 1, "unit_price_paisa": 125_000}],
                },
                headers=auth_header(session),
            )

        detail = await client.get(f"/v1/customers/{customer['id']}", headers=auth_header(session))
        assert len(detail.json()["addresses"]) == 1


class TestCustomerTenantIsolation:
    async def test_one_shop_cannot_read_anothers_customer(self, client: AsyncClient) -> None:
        first = await signed_in_shop(client, "01744400033", shop_name="Shop C")
        second = await signed_in_shop(client, "01744400044", shop_name="Shop D")

        customer = await create_customer(client, first, phone="01712345678")

        response = await client.get(f"/v1/customers/{customer['id']}", headers=auth_header(second))
        assert response.status_code == 404

    async def test_one_shop_cannot_reveal_anothers_customer_phone(
        self, client: AsyncClient
    ) -> None:
        first = await signed_in_shop(client, "01744400055", shop_name="Shop E")
        second = await signed_in_shop(client, "01744400066", shop_name="Shop F")

        customer = await create_customer(client, first, phone="01712345678")

        response = await client.post(
            f"/v1/customers/{customer['id']}/reveal-phone",
            json={"reason": "attempted cross-tenant read"},
            headers=auth_header(second),
        )
        assert response.status_code == 404

    async def test_customer_lists_never_mix_shops(self, client: AsyncClient) -> None:
        first = await signed_in_shop(client, "01744400077", shop_name="Shop G")
        second = await signed_in_shop(client, "01744400088", shop_name="Shop H")

        await create_customer(client, first, phone="01712345678", name="In G")
        await create_customer(client, second, phone="01819223344", name="In H")

        listed = await client.get("/v1/customers", headers=auth_header(second))
        assert [row["name"] for row in listed.json()["items"]] == ["In H"]

    async def test_lookup_does_not_leak_across_shops(self, client: AsyncClient) -> None:
        # The same person, known to shop I, must be invisible to shop J.
        first = await signed_in_shop(client, "01744400099", shop_name="Shop I")
        second = await signed_in_shop(client, "01744401010", shop_name="Shop J")

        await create_customer(client, first, phone="01712345678", name="Known to I")

        response = await client.get(
            "/v1/customers/lookup?phone=01712345678", headers=auth_header(second)
        )
        assert response.status_code == 200
        assert response.json() is None
