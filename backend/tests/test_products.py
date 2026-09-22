"""Products and the stock movement ledger.

The invariant these tests protect: **the running total and the ledger can never
disagree**. Master spec section 10.4 forbids stock being a settable number, and
a seller whose stock silently drifts loses money on parcels they cannot fulfil.
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import AsyncClient

from tests.conftest_commerce import create_product, signed_in_shop
from tests.test_auth_flow import auth_header


class TestProductCrud:
    async def test_create_and_read(self, client: AsyncClient, unique_phone: str) -> None:
        session = await signed_in_shop(client, unique_phone)
        product = await create_product(
            client, session, name="Red Abaya", sku="ABA-RED", opening_stock=18
        )

        assert product["name"] == "Red Abaya"
        assert product["sku"] == "ABA-RED"
        assert product["cost_paisa"] == 65_000
        assert product["stock_on_hand"] == 18

        response = await client.get(f"/v1/products/{product['id']}", headers=auth_header(session))
        assert response.status_code == 200
        assert response.json()["id"] == product["id"]

    async def test_opening_stock_is_a_ledger_entry(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        # Even the initial count has provenance: "where did this number come
        # from" always has an answer.
        session = await signed_in_shop(client, unique_phone)
        product = await create_product(client, session, opening_stock=20)

        response = await client.get(
            f"/v1/products/{product['id']}/stock-movements",
            headers=auth_header(session),
        )
        movements = response.json()["items"]
        assert len(movements) == 1
        assert movements[0]["reason"] == "OPENING"
        assert movements[0]["quantity_delta"] == 20
        assert movements[0]["balance_after"] == 20

    async def test_sku_is_unique_within_a_shop(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        await create_product(client, session, name="First", sku="DUP-1")

        response = await client.post(
            "/v1/products",
            json={"name": "Second", "sku": "DUP-1"},
            headers=auth_header(session),
        )
        assert response.status_code == 409
        assert response.json()["code"] == "CONFLICT"

    async def test_the_same_sku_is_free_in_another_shop(self, client: AsyncClient) -> None:
        # SKUs are scoped per tenant, so two sellers can both use "ABA-BLK".
        first = await signed_in_shop(client, "01755500011", shop_name="Shop A")
        second = await signed_in_shop(client, "01755500022", shop_name="Shop B")

        await create_product(client, first, sku="SHARED-SKU")
        await create_product(client, second, sku="SHARED-SKU")

    async def test_products_without_a_sku_do_not_collide(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        await create_product(client, session, name="No SKU 1")
        await create_product(client, session, name="No SKU 2")

    async def test_changing_cost_does_not_touch_stock(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        product = await create_product(client, session, opening_stock=5)

        response = await client.patch(
            f"/v1/products/{product['id']}",
            json={"cost_paisa": 70_000},
            headers=auth_header(session),
        )
        assert response.status_code == 200
        assert response.json()["cost_paisa"] == 70_000
        assert response.json()["stock_on_hand"] == 5

    async def test_archiving_keeps_the_product_readable(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        # Archived, not deleted: a product referenced by a historical order must
        # stay readable or that order's economics become unexplainable.
        session = await signed_in_shop(client, unique_phone)
        product = await create_product(client, session)

        await client.patch(
            f"/v1/products/{product['id']}",
            json={"archived": True},
            headers=auth_header(session),
        )
        response = await client.get(f"/v1/products/{product['id']}", headers=auth_header(session))
        assert response.status_code == 200
        assert response.json()["is_archived"] is True

        listed = await client.get("/v1/products", headers=auth_header(session))
        assert product["id"] not in [row["id"] for row in listed.json()["items"]]

    async def test_low_stock_flag(self, client: AsyncClient, unique_phone: str) -> None:
        session = await signed_in_shop(client, unique_phone)
        product = await create_product(client, session, opening_stock=3, low_stock_threshold=5)
        assert product["is_low_stock"] is True


class TestStockLedger:
    async def test_adjustment_appends_and_updates_the_balance(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        product = await create_product(client, session, opening_stock=10)

        response = await client.post(
            f"/v1/products/{product['id']}/stock-adjustments",
            json={"quantity_delta": -3, "note": "Damaged in storage"},
            headers=auth_header(session),
        )
        assert response.status_code == 201
        assert response.json()["balance_after"] == 7

        current = await client.get(f"/v1/products/{product['id']}", headers=auth_header(session))
        assert current.json()["stock_on_hand"] == 7

    async def test_a_manual_adjustment_requires_a_note(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        # An unexplained hand-correction is indistinguishable from a bug later.
        session = await signed_in_shop(client, unique_phone)
        product = await create_product(client, session, opening_stock=10)

        response = await client.post(
            f"/v1/products/{product['id']}/stock-adjustments",
            json={"quantity_delta": -2},
            headers=auth_header(session),
        )
        assert response.status_code == 422
        assert "note" in response.json()["message_en"].lower()

    async def test_stock_cannot_go_negative_without_permission(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        product = await create_product(client, session, opening_stock=2)

        response = await client.post(
            f"/v1/products/{product['id']}/stock-adjustments",
            json={"quantity_delta": -5, "note": "Oversold"},
            headers=auth_header(session),
        )
        assert response.status_code == 409
        assert response.json()["details"]["stock_on_hand"] == 2

    async def test_overselling_is_possible_when_stated_explicitly(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        # Master spec section 76: oversell is a seller decision, not something
        # to silently permit or silently block.
        session = await signed_in_shop(client, unique_phone)
        product = await create_product(client, session, opening_stock=2)

        response = await client.post(
            f"/v1/products/{product['id']}/stock-adjustments",
            json={
                "quantity_delta": -5,
                "note": "Pre-order accepted",
                "allow_negative": True,
            },
            headers=auth_header(session),
        )
        assert response.status_code == 201
        assert response.json()["balance_after"] == -3

    async def test_a_restore_reason_cannot_remove_stock(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        # The reason and the sign must agree, or the ledger lies about what
        # happened.
        session = await signed_in_shop(client, unique_phone)
        product = await create_product(client, session, opening_stock=10)

        response = await client.post(
            f"/v1/products/{product['id']}/stock-adjustments",
            json={"quantity_delta": 5, "reason": "DAMAGED_WRITE_OFF", "note": "x"},
            headers=auth_header(session),
        )
        assert response.status_code == 422

    async def test_system_reasons_are_rejected_from_the_seller_endpoint(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        # A client must not be able to fabricate a booking decrement that no
        # booking produced.
        session = await signed_in_shop(client, unique_phone)
        product = await create_product(client, session, opening_stock=10)

        response = await client.post(
            f"/v1/products/{product['id']}/stock-adjustments",
            json={"quantity_delta": -1, "reason": "BOOKED_DECREMENT"},
            headers=auth_header(session),
        )
        assert response.status_code == 422

    async def test_history_is_ordered_newest_first(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        product = await create_product(client, session, opening_stock=10)

        for delta in (-1, -2, 5):
            await client.post(
                f"/v1/products/{product['id']}/stock-adjustments",
                json={"quantity_delta": delta, "note": "adjust"},
                headers=auth_header(session),
            )

        response = await client.get(
            f"/v1/products/{product['id']}/stock-movements",
            headers=auth_header(session),
        )
        movements = response.json()["items"]
        assert len(movements) == 4
        assert movements[0]["quantity_delta"] == 5
        assert movements[0]["balance_after"] == 12

    async def test_the_balance_always_equals_the_sum_of_movements(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        """The core invariant: the cached total is exactly the ledger's sum."""
        session = await signed_in_shop(client, unique_phone)
        product = await create_product(client, session, opening_stock=100)

        for delta in (-7, -3, 12, -20, 5, -1):
            await client.post(
                f"/v1/products/{product['id']}/stock-adjustments",
                json={"quantity_delta": delta, "note": "adjust"},
                headers=auth_header(session),
            )

        movements = (
            await client.get(
                f"/v1/products/{product['id']}/stock-movements?limit=100",
                headers=auth_header(session),
            )
        ).json()["items"]
        ledger_total = sum(movement["quantity_delta"] for movement in movements)

        current = await client.get(f"/v1/products/{product['id']}", headers=auth_header(session))
        assert current.json()["stock_on_hand"] == ledger_total == 86

    async def test_concurrent_adjustments_do_not_lose_one(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        """Master spec section 76: concurrent adjustments must serialise.

        Two devices decrementing at the same moment must both land. A
        read-modify-write without a row lock loses one of them, and the seller's
        stock silently drifts upward.
        """
        session = await signed_in_shop(client, unique_phone)
        product = await create_product(client, session, opening_stock=50)

        async def adjust(delta: int) -> int:
            response = await client.post(
                f"/v1/products/{product['id']}/stock-adjustments",
                json={"quantity_delta": delta, "note": "concurrent"},
                headers=auth_header(session),
            )
            return response.status_code

        results = await asyncio.gather(*(adjust(-1) for _ in range(5)))
        assert all(code == 201 for code in results)

        current = await client.get(f"/v1/products/{product['id']}", headers=auth_header(session))
        assert current.json()["stock_on_hand"] == 45

        movements = (
            await client.get(
                f"/v1/products/{product['id']}/stock-movements?limit=100",
                headers=auth_header(session),
            )
        ).json()["items"]
        # Every attempt is recorded, and the balances form an unbroken chain.
        assert len(movements) == 6
        balances = sorted(m["balance_after"] for m in movements)
        assert balances == [45, 46, 47, 48, 49, 50]


class TestProductSearchAndPaging:
    async def test_search_matches_name_and_sku(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        await create_product(client, session, name="Black Abaya", sku="ABA-BLK")
        await create_product(client, session, name="Watch X1", sku="WAT-X1")

        by_name = await client.get("/v1/products?search=abaya", headers=auth_header(session))
        assert [row["name"] for row in by_name.json()["items"]] == ["Black Abaya"]

        by_sku = await client.get("/v1/products?search=wat-x1", headers=auth_header(session))
        assert [row["name"] for row in by_sku.json()["items"]] == ["Watch X1"]

    async def test_cursor_pagination_walks_every_row_once(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        for index in range(7):
            await create_product(client, session, name=f"Product {index}")

        seen: list[str] = []
        cursor: str | None = None
        for _ in range(5):
            url = "/v1/products?limit=3" + (f"&cursor={cursor}" if cursor else "")
            page = (await client.get(url, headers=auth_header(session))).json()
            seen.extend(row["id"] for row in page["items"])
            cursor = page["next_cursor"]
            if not cursor:
                break

        assert len(seen) == 7
        assert len(set(seen)) == 7, "cursor pagination must not repeat a row"


class TestProductTenantIsolation:
    async def test_one_shop_cannot_read_anothers_product(self, client: AsyncClient) -> None:
        first = await signed_in_shop(client, "01766600011", shop_name="Shop One")
        second = await signed_in_shop(client, "01766600022", shop_name="Shop Two")

        product = await create_product(client, first, name="Private Product")

        response = await client.get(f"/v1/products/{product['id']}", headers=auth_header(second))
        # Reported as 404, not 403: a 403 would confirm the row exists.
        assert response.status_code == 404

    async def test_one_shop_cannot_adjust_anothers_stock(self, client: AsyncClient) -> None:
        first = await signed_in_shop(client, "01766600033", shop_name="Shop Three")
        second = await signed_in_shop(client, "01766600044", shop_name="Shop Four")

        product = await create_product(client, first, opening_stock=10)

        response = await client.post(
            f"/v1/products/{product['id']}/stock-adjustments",
            json={"quantity_delta": -5, "note": "attack"},
            headers=auth_header(second),
        )
        assert response.status_code == 404

        unchanged = await client.get(f"/v1/products/{product['id']}", headers=auth_header(first))
        assert unchanged.json()["stock_on_hand"] == 10

    async def test_product_lists_never_mix_shops(self, client: AsyncClient) -> None:
        first = await signed_in_shop(client, "01766600055", shop_name="Shop Five")
        second = await signed_in_shop(client, "01766600066", shop_name="Shop Six")

        await create_product(client, first, name="Only In Five")
        await create_product(client, second, name="Only In Six")

        listed = await client.get("/v1/products", headers=auth_header(second))
        names = [row["name"] for row in listed.json()["items"]]
        assert names == ["Only In Six"]

    async def test_movements_of_another_shop_are_not_reachable(self, client: AsyncClient) -> None:
        first = await signed_in_shop(client, "01766600077", shop_name="Shop Seven")
        second = await signed_in_shop(client, "01766600088", shop_name="Shop Eight")

        product = await create_product(client, first, opening_stock=9)

        response = await client.get(
            f"/v1/products/{product['id']}/stock-movements",
            headers=auth_header(second),
        )
        assert response.status_code == 404


class TestProductAuth:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("get", "/v1/products"),
            ("post", "/v1/products"),
        ],
    )
    async def test_products_require_authentication(
        self, client: AsyncClient, method: str, path: str
    ) -> None:
        response = await client.request(method.upper(), path, json={})
        assert response.status_code == 401
