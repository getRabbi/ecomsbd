"""Orders, order numbering, snapshots and duplicate detection.

Master spec sections 9, 10.1, 18.2, 62.17 and 70.

The properties that matter most here are the ones that cost money if wrong:
an order must never book a courier by itself, an order's economics must not
change when a product is edited later, and an order number must never be reused.
"""

from __future__ import annotations

import asyncio

from httpx import AsyncClient

from tests.conftest_commerce import create_order, create_product, signed_in_shop
from tests.test_auth_flow import auth_header


class TestOrderCreation:
    async def test_a_new_order_starts_as_a_draft(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        result = await create_order(client, session)
        order = result["order"]

        assert order["status"] == "DRAFT"
        assert order["cod_amount_paisa"] == 125_000
        assert len(order["items"]) == 1

    async def test_creating_an_order_never_books_a_courier(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        """Master spec section 62.17.

        Booking is an irreversible external action and is never a side effect of
        saving a record. The response says so explicitly rather than leaving the
        client to assume.
        """
        session = await signed_in_shop(client, unique_phone)
        order = (await create_order(client, session))["order"]

        assert order["fulfillment_state"] == "NOT_BOOKED"
        # Nothing is fabricated for engines that do not exist yet.
        assert order["risk_state"] == "NOT_CHECKED"
        assert order["profit_state"] == "PENDING_CALCULATION"

    async def test_the_customer_is_created_from_the_phone(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        result = await create_order(client, session, phone="01712345678", customer_name="Nusrat")

        assert result["order"]["customer_id"] is not None
        assert result["order"]["customer_phone_masked"] == "01712****78"

        lookup = await client.get(
            "/v1/customers/lookup?phone=01712345678", headers=auth_header(session)
        )
        assert lookup.json()["name"] == "Nusrat"

    async def test_cod_defaults_to_the_computed_total(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        result = await create_order(
            client,
            session,
            items=[
                {"name": "Abaya", "quantity": 2, "unit_price_paisa": 125_000},
                {"name": "Bag", "quantity": 1, "unit_price_paisa": 99_000},
            ],
            delivery_fee_paisa=8_000,
            discount_paisa=5_000,
        )
        order = result["order"]
        # 2×1250 + 990 = 3490, minus 50 discount, plus 80 delivery = 3520
        assert order["subtotal_paisa"] == 349_000
        assert order["cod_amount_paisa"] == 352_000

    async def test_an_explicit_cod_amount_wins(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        # Sellers negotiate. The typed figure is the one the courier collects.
        session = await signed_in_shop(client, unique_phone)
        result = await create_order(client, session, cod_amount_paisa=100_000)
        assert result["order"]["cod_amount_paisa"] == 100_000

    async def test_an_order_needs_at_least_one_item(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        response = await client.post(
            "/v1/orders",
            json={"phone": "01712345678", "items": []},
            headers=auth_header(session),
        )
        assert response.status_code == 422

    async def test_an_invalid_phone_is_rejected(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        response = await client.post(
            "/v1/orders",
            json={
                "phone": "01012345678",
                "items": [{"name": "Abaya", "quantity": 1, "unit_price_paisa": 1}],
            },
            headers=auth_header(session),
        )
        assert response.status_code in (422, 400)

    async def test_a_free_text_item_needs_no_product(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        # Master spec section 20 allows an "optional free-text order item":
        # sellers add things that are not in their catalogue.
        session = await signed_in_shop(client, unique_phone)
        result = await create_order(
            client,
            session,
            items=[{"name": "Custom gift wrap", "quantity": 1, "unit_price_paisa": 5_000}],
        )
        assert result["order"]["items"][0]["product_id"] is None


class TestOrderSnapshots:
    async def test_item_economics_are_frozen_at_creation(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        """Master spec section 18.2.

        Changing a product's cost tomorrow must not rewrite the profit on an
        order sold today. This is the single most load-bearing property of the
        commerce core for everything the money engine will compute later.
        """
        session = await signed_in_shop(client, unique_phone)
        product = await create_product(client, session, cost_paisa=65_000, price_paisa=125_000)

        result = await create_order(
            client, session, items=[{"product_id": product["id"], "quantity": 2}]
        )
        order_id = result["order"]["id"]
        item = result["order"]["items"][0]
        assert item["unit_cost_snapshot_paisa"] == 65_000
        assert item["unit_price_paisa"] == 125_000

        # The supplier raises their price.
        await client.patch(
            f"/v1/products/{product['id']}",
            json={"cost_paisa": 90_000, "default_selling_price_paisa": 150_000},
            headers=auth_header(session),
        )

        unchanged = await client.get(f"/v1/orders/{order_id}", headers=auth_header(session))
        item_after = unchanged.json()["items"][0]
        assert item_after["unit_cost_snapshot_paisa"] == 65_000
        assert item_after["unit_price_paisa"] == 125_000

    async def test_the_product_name_survives_archiving(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        product = await create_product(client, session, name="Discontinued Abaya")
        result = await create_order(
            client, session, items=[{"product_id": product["id"], "quantity": 1}]
        )

        await client.patch(
            f"/v1/products/{product['id']}",
            json={"archived": True},
            headers=auth_header(session),
        )

        order = await client.get(
            f"/v1/orders/{result['order']['id']}", headers=auth_header(session)
        )
        assert order.json()["items"][0]["product_name"] == "Discontinued Abaya"


class TestOrderNumbering:
    async def test_the_format_matches_the_spec(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        order = (await create_order(client, session))["order"]

        # CP-20260909-0042. The CP- prefix is a stored technical identifier,
        # deliberately unchanged by the ecomsbd rebrand (master spec section 70).
        assert order["order_number"].startswith("CP-")
        parts = order["order_number"].split("-")
        assert len(parts) == 3
        assert len(parts[1]) == 8 and parts[1].isdigit()
        assert len(parts[2]) == 4 and parts[2].isdigit()

    async def test_numbers_increment_within_a_shop(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        numbers = [(await create_order(client, session))["order"]["order_number"] for _ in range(3)]
        sequences = [int(number.split("-")[2]) for number in numbers]
        assert sequences == [1, 2, 3]

    async def test_two_shops_number_independently(self, client: AsyncClient) -> None:
        first = await signed_in_shop(client, "01733300011", shop_name="Shop K")
        second = await signed_in_shop(client, "01733300022", shop_name="Shop L")

        a = (await create_order(client, first))["order"]["order_number"]
        b = (await create_order(client, second))["order"]["order_number"]
        assert a.endswith("-0001")
        assert b.endswith("-0001")

    async def test_a_cancelled_number_is_never_reused(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        # Master spec section 70. A courier statement carrying a retired number
        # must never match a different order.
        session = await signed_in_shop(client, unique_phone)
        first = (await create_order(client, session))["order"]

        await client.patch(
            f"/v1/orders/{first['id']}",
            json={"status": "CANCELLED", "cancellation_reason": "Customer changed mind"},
            headers=auth_header(session),
        )

        second = (await create_order(client, session))["order"]
        assert second["order_number"] != first["order_number"]
        assert int(second["order_number"].split("-")[2]) == 2

    async def test_concurrent_creation_produces_unique_numbers(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        """Two devices creating orders at the same instant must not collide.

        Allocation reads the current maximum without a lock, so a collision is
        expected; the unique constraint plus a bounded retry is what makes it
        safe. This test proves the retry works rather than assuming it.
        """
        session = await signed_in_shop(client, unique_phone)

        async def place(index: int) -> str:
            result = await create_order(client, session, phone=f"0171234{index:04d}")
            return result["order"]["order_number"]

        numbers = await asyncio.gather(*(place(i) for i in range(5)))
        assert len(set(numbers)) == 5, f"order numbers collided: {numbers}"


class TestOrderLifecycle:
    async def test_a_legal_transition_is_applied(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        order = (await create_order(client, session))["order"]

        response = await client.patch(
            f"/v1/orders/{order['id']}",
            json={"status": "CONFIRMED"},
            headers=auth_header(session),
        )
        assert response.status_code == 200
        assert response.json()["status"] == "CONFIRMED"

    async def test_an_illegal_transition_is_refused(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        # DRAFT cannot jump to COMPLETED: that would skip the states which
        # create stock movements and receivables.
        session = await signed_in_shop(client, unique_phone)
        order = (await create_order(client, session))["order"]

        response = await client.patch(
            f"/v1/orders/{order['id']}",
            json={"status": "COMPLETED"},
            headers=auth_header(session),
        )
        assert response.status_code == 409

    async def test_a_cancelled_order_cannot_be_revived(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        order = (await create_order(client, session))["order"]

        await client.patch(
            f"/v1/orders/{order['id']}",
            json={"status": "CANCELLED", "cancellation_reason": "done"},
            headers=auth_header(session),
        )
        response = await client.patch(
            f"/v1/orders/{order['id']}",
            json={"status": "CONFIRMED"},
            headers=auth_header(session),
        )
        assert response.status_code == 409

    async def test_edits_bump_the_version(self, client: AsyncClient, unique_phone: str) -> None:
        session = await signed_in_shop(client, unique_phone)
        order = (await create_order(client, session))["order"]
        assert order["version"] == 1

        response = await client.patch(
            f"/v1/orders/{order['id']}",
            json={"note": "Call before delivery"},
            headers=auth_header(session),
        )
        assert response.json()["version"] == 2

    async def test_a_stale_version_is_a_conflict_not_an_overwrite(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        # Master spec section 37: a client editing an old copy is told, never
        # silently allowed to clobber someone else's change.
        session = await signed_in_shop(client, unique_phone)
        order = (await create_order(client, session))["order"]

        await client.patch(
            f"/v1/orders/{order['id']}",
            json={"note": "First edit"},
            headers=auth_header(session),
        )

        response = await client.patch(
            f"/v1/orders/{order['id']}",
            json={"note": "Stale edit", "expected_version": 1},
            headers=auth_header(session),
        )
        assert response.status_code == 409
        assert response.json()["details"]["version"] == 2


class TestDuplicateDetection:
    async def test_a_similar_recent_order_is_flagged(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        await create_order(client, session, phone="01712345678")
        result = await create_order(client, session, phone="01712345678")

        check = result["duplicate_check"]
        assert check["possible_duplicate"] is True
        assert check["message"] == "Similar order found recently."
        assert check["candidates"][0]["is_strong"] is True
        assert "IDENTICAL_AMOUNT" in check["candidates"][0]["reasons"]

    async def test_a_duplicate_warning_never_blocks_the_order(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        """Master spec section 9: "this is a warning, not a hard block."

        A customer ordering twice in a day is a real workflow. The seller is the
        one who can tell a repeat purchase from a mistake.
        """
        session = await signed_in_shop(client, unique_phone)
        first = await create_order(client, session, phone="01712345678")
        second = await create_order(client, session, phone="01712345678")

        assert second["duplicate_check"]["possible_duplicate"] is True
        assert second["order"]["id"] != first["order"]["id"]
        assert second["order"]["status"] == "DRAFT"

    async def test_a_different_phone_is_not_a_duplicate(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        await create_order(client, session, phone="01712345678")
        result = await create_order(client, session, phone="01819223344")
        assert result["duplicate_check"]["possible_duplicate"] is False

    async def test_the_same_phone_with_a_different_amount_is_not_flagged(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        # A repeat customer buying something else is not a duplicate; warning on
        # it would train sellers to ignore the warning.
        session = await signed_in_shop(client, unique_phone)
        await create_order(client, session, phone="01712345678", cod_amount_paisa=125_000)
        result = await create_order(
            client,
            session,
            phone="01712345678",
            cod_amount_paisa=980_000,
            items=[{"name": "Totally different", "quantity": 1, "unit_price_paisa": 980_000}],
        )
        assert result["duplicate_check"]["possible_duplicate"] is False

    async def test_a_cancelled_order_is_not_a_duplicate(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        # Re-entering an order the seller just cancelled is the correct action.
        session = await signed_in_shop(client, unique_phone)
        first = await create_order(client, session, phone="01712345678")
        await client.patch(
            f"/v1/orders/{first['order']['id']}",
            json={"status": "CANCELLED", "cancellation_reason": "mistake"},
            headers=auth_header(session),
        )

        result = await create_order(client, session, phone="01712345678")
        assert result["duplicate_check"]["possible_duplicate"] is False

    async def test_the_probe_endpoint_warns_before_committing(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        # Called while the form is open, so the warning arrives in time.
        session = await signed_in_shop(client, unique_phone)
        await create_order(client, session, phone="01712345678")

        response = await client.post(
            "/v1/orders/check-duplicates",
            json={
                "phone": "01712345678",
                "cod_amount_paisa": 125_000,
                "item_names": ["Black Abaya XL"],
            },
            headers=auth_header(session),
        )
        assert response.status_code == 200
        assert response.json()["possible_duplicate"] is True

    async def test_duplicates_do_not_leak_across_shops(self, client: AsyncClient) -> None:
        first = await signed_in_shop(client, "01733300033", shop_name="Shop M")
        second = await signed_in_shop(client, "01733300044", shop_name="Shop N")

        await create_order(client, first, phone="01712345678")
        result = await create_order(client, second, phone="01712345678")
        assert result["duplicate_check"]["possible_duplicate"] is False


class TestOfflineReplaySafety:
    async def test_the_same_client_id_creates_one_order(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        """A device replaying its queue must not create a second order.

        The most consequential idempotency case in the commerce core: a seller
        on a bad connection sends the same queued order twice, and two parcels
        would be booked from it later.
        """
        session = await signed_in_shop(client, unique_phone)
        client_id = "01a08000-0000-7000-8000-00000000abcd"

        first = await create_order(client, session, client_id=client_id)
        second = await create_order(client, session, client_id=client_id)

        assert first["order"]["id"] == second["order"]["id"]
        assert first["order"]["order_number"] == second["order"]["order_number"]

        listed = await client.get("/v1/orders", headers=auth_header(session))
        assert len(listed.json()["items"]) == 1


class TestOrderSearchAndIsolation:
    async def test_search_by_order_number_and_phone(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        order = (await create_order(client, session, phone="01712345678"))["order"]

        by_number = await client.get(
            f"/v1/orders?search={order['order_number']}", headers=auth_header(session)
        )
        assert [row["id"] for row in by_number.json()["items"]] == [order["id"]]

        by_phone = await client.get("/v1/orders?search=01712345678", headers=auth_header(session))
        assert [row["id"] for row in by_phone.json()["items"]] == [order["id"]]

    async def test_search_normalises_bangla_numerals(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        # Master spec section 129.
        session = await signed_in_shop(client, unique_phone)
        order = (await create_order(client, session, phone="01712345678"))["order"]

        response = await client.get("/v1/orders?search=০১৭১২৩৪৫৬৭৮", headers=auth_header(session))
        assert [row["id"] for row in response.json()["items"]] == [order["id"]]

    async def test_filter_by_status(self, client: AsyncClient, unique_phone: str) -> None:
        session = await signed_in_shop(client, unique_phone)
        first = (await create_order(client, session, phone="01712345678"))["order"]
        await create_order(client, session, phone="01819223344")

        await client.patch(
            f"/v1/orders/{first['id']}",
            json={"status": "CONFIRMED"},
            headers=auth_header(session),
        )

        response = await client.get("/v1/orders?status=CONFIRMED", headers=auth_header(session))
        assert [row["id"] for row in response.json()["items"]] == [first["id"]]

    async def test_one_shop_cannot_read_anothers_order(self, client: AsyncClient) -> None:
        first = await signed_in_shop(client, "01733300055", shop_name="Shop O")
        second = await signed_in_shop(client, "01733300066", shop_name="Shop P")

        order = (await create_order(client, first))["order"]

        response = await client.get(f"/v1/orders/{order['id']}", headers=auth_header(second))
        assert response.status_code == 404

    async def test_one_shop_cannot_edit_anothers_order(self, client: AsyncClient) -> None:
        first = await signed_in_shop(client, "01733300077", shop_name="Shop Q")
        second = await signed_in_shop(client, "01733300088", shop_name="Shop R")

        order = (await create_order(client, first))["order"]

        response = await client.patch(
            f"/v1/orders/{order['id']}",
            json={"status": "CANCELLED", "cancellation_reason": "attack"},
            headers=auth_header(second),
        )
        assert response.status_code == 404

        unchanged = await client.get(f"/v1/orders/{order['id']}", headers=auth_header(first))
        assert unchanged.json()["status"] == "DRAFT"

    async def test_order_lists_never_mix_shops(self, client: AsyncClient) -> None:
        first = await signed_in_shop(client, "01733300099", shop_name="Shop S")
        second = await signed_in_shop(client, "01733301010", shop_name="Shop T")

        await create_order(client, first)
        await create_order(client, second)

        listed = await client.get("/v1/orders", headers=auth_header(second))
        assert len(listed.json()["items"]) == 1

    async def test_an_order_cannot_reference_another_shops_product(
        self, client: AsyncClient
    ) -> None:
        first = await signed_in_shop(client, "01733301111", shop_name="Shop U")
        second = await signed_in_shop(client, "01733301122", shop_name="Shop V")

        product = await create_product(client, first, name="Private Product")

        response = await client.post(
            "/v1/orders",
            json={
                "phone": "01712345678",
                "items": [{"product_id": product["id"], "quantity": 1}],
            },
            headers=auth_header(second),
        )
        assert response.status_code == 404
