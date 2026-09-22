"""Import pipeline and offline sync.

Master spec sections 37, 38 and 98.

The two properties that matter: an import never silently coerces bad data, and
a resent device queue never creates anything twice.
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient

from tests.conftest_commerce import create_product, signed_in_shop
from tests.test_auth_flow import auth_header

PRODUCTS_CSV = (
    "Product Name,SKU,Cost Price,Selling Price,Stock\n"
    "Black Abaya,ABA-BLK,650,1250,12\n"
    "Red Abaya,ABA-RED,630,1250,18\n"
    "Watch X1,WAT-X1,890,1850,3\n"
)

ORDERS_CSV = (
    "Customer,Mobile,Address,Product,Qty,COD Amount\n"
    "Nusrat Jahan,01712345678,Mirpur 10 Dhaka,Black Abaya XL,1,1250\n"
    "Rafi Hasan,01819223344,Uttara Dhaka,Watch X1,1,1850\n"
)


async def upload(
    client: AsyncClient, session: dict, *, template: str, content: str, filename: str
) -> dict:
    response = await client.post(
        "/v1/imports",
        files={"file": (filename, content.encode(), "text/csv")},
        data={"template": template},
        headers=auth_header(session),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def dry_run(client: AsyncClient, session: dict, import_id: str) -> dict:
    response = await client.post(
        f"/v1/imports/{import_id}/dry-run", json={}, headers=auth_header(session)
    )
    assert response.status_code == 200, response.text
    return response.json()


class TestImportPipeline:
    async def test_upload_detects_headers_and_suggests_a_mapping(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        batch = await upload(
            client, session, template="PRODUCTS", content=PRODUCTS_CSV, filename="p.csv"
        )

        assert batch["status"] == "UPLOADED"
        assert batch["row_count"] == 3
        assert "Product Name" in batch["detected_headers"]
        # The mapping is a suggestion the seller can correct, not a decision.
        assert batch["column_mapping"]["name"] == "Product Name"
        assert batch["column_mapping"]["cost"] == "Cost Price"
        assert batch["column_mapping"]["price"] == "Selling Price"

    async def test_dry_run_writes_nothing(self, client: AsyncClient, unique_phone: str) -> None:
        session = await signed_in_shop(client, unique_phone)
        batch = await upload(
            client, session, template="PRODUCTS", content=PRODUCTS_CSV, filename="p.csv"
        )
        report = await dry_run(client, session, batch["id"])

        assert report["status"] == "VALIDATED"
        assert report["ready_count"] == 3
        assert report["invalid_count"] == 0
        assert report["can_commit"] is True

        listed = await client.get("/v1/products", headers=auth_header(session))
        assert listed.json()["items"] == []

    async def test_commit_creates_the_records(self, client: AsyncClient, unique_phone: str) -> None:
        session = await signed_in_shop(client, unique_phone)
        batch = await upload(
            client, session, template="PRODUCTS", content=PRODUCTS_CSV, filename="p.csv"
        )
        await dry_run(client, session, batch["id"])

        response = await client.post(
            f"/v1/imports/{batch['id']}/commit", headers=auth_header(session)
        )
        assert response.status_code == 200
        assert response.json()["created_count"] == 3

        listed = await client.get("/v1/products", headers=auth_header(session))
        products = {row["name"]: row for row in listed.json()["items"]}
        assert set(products) == {"Black Abaya", "Red Abaya", "Watch X1"}
        assert products["Black Abaya"]["cost_paisa"] == 65_000
        assert products["Black Abaya"]["stock_on_hand"] == 12

    async def test_imported_stock_is_a_ledger_entry(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        # An imported opening balance has the same provenance as a typed one.
        session = await signed_in_shop(client, unique_phone)
        batch = await upload(
            client, session, template="PRODUCTS", content=PRODUCTS_CSV, filename="p.csv"
        )
        await dry_run(client, session, batch["id"])
        await client.post(f"/v1/imports/{batch['id']}/commit", headers=auth_header(session))

        listed = await client.get("/v1/products", headers=auth_header(session))
        product = listed.json()["items"][0]
        movements = await client.get(
            f"/v1/products/{product['id']}/stock-movements",
            headers=auth_header(session),
        )
        assert movements.json()["items"][0]["reason"] == "OPENING"

    async def test_orders_import(self, client: AsyncClient, unique_phone: str) -> None:
        session = await signed_in_shop(client, unique_phone)
        batch = await upload(
            client, session, template="ORDERS", content=ORDERS_CSV, filename="o.csv"
        )
        report = await dry_run(client, session, batch["id"])
        assert report["ready_count"] == 2

        await client.post(f"/v1/imports/{batch['id']}/commit", headers=auth_header(session))
        orders = await client.get("/v1/orders", headers=auth_header(session))
        assert len(orders.json()["items"]) == 2
        assert orders.json()["items"][0]["channel"] == "CSV_IMPORT"

    async def test_commit_requires_a_dry_run_first(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        batch = await upload(
            client, session, template="PRODUCTS", content=PRODUCTS_CSV, filename="p.csv"
        )
        response = await client.post(
            f"/v1/imports/{batch['id']}/commit", headers=auth_header(session)
        )
        assert response.status_code == 409


class TestImportValidation:
    async def test_an_invalid_phone_fails_its_row_and_is_not_coerced(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        """Master spec section 98: "do not silently coerce invalid phone values."

        A row with a bad number must be reported, not saved with a blank phone —
        a customer with no contact number is discovered months later.
        """
        session = await signed_in_shop(client, unique_phone)
        csv_text = (
            "Customer,Mobile,Address,Product,COD Amount\n"
            "Good,01712345678,Mirpur,Abaya,1250\n"
            "Bad,not-a-phone,Uttara,Watch,1850\n"
            "AlsoBad,01012345678,Savar,Bag,990\n"
        )
        batch = await upload(client, session, template="ORDERS", content=csv_text, filename="o.csv")
        report = await dry_run(client, session, batch["id"])

        assert report["ready_count"] == 1
        assert report["invalid_count"] == 2

        rows = await client.get(
            f"/v1/imports/{batch['id']}/rows?status=INVALID",
            headers=auth_header(session),
        )
        errors = rows.json()
        assert len(errors) == 2
        assert errors[0]["errors"][0]["field"] == "phone"
        # The offending value is shown so the seller can fix their sheet.
        assert "not-a-phone" in errors[0]["errors"][0]["message"]

    async def test_an_invalid_amount_fails_its_row(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        csv_text = (
            "Product Name,Cost Price,Selling Price\n"
            "Good Product,650,1250\n"
            "Bad Product,not-a-number,1250\n"
        )
        batch = await upload(
            client, session, template="PRODUCTS", content=csv_text, filename="p.csv"
        )
        report = await dry_run(client, session, batch["id"])

        assert report["ready_count"] == 1
        assert report["invalid_count"] == 1

    async def test_only_valid_rows_are_created(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        # One bad row must not cost the seller the good ones.
        session = await signed_in_shop(client, unique_phone)
        csv_text = "Product Name,Selling Price\nGood A,1250\nBad,oops\nGood B,990\n"
        batch = await upload(
            client, session, template="PRODUCTS", content=csv_text, filename="p.csv"
        )
        await dry_run(client, session, batch["id"])
        response = await client.post(
            f"/v1/imports/{batch['id']}/commit", headers=auth_header(session)
        )
        assert response.json()["created_count"] == 2

    async def test_a_missing_required_column_is_reported_before_validating(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        batch = await upload(
            client,
            session,
            template="ORDERS",
            content="Something,Else\nfoo,bar\n",
            filename="o.csv",
        )
        response = await client.post(
            f"/v1/imports/{batch['id']}/dry-run", json={}, headers=auth_header(session)
        )
        assert response.status_code == 422
        assert "phone" in response.json()["details"]["missing"]

    async def test_an_xlsx_upload_is_reported_not_half_parsed(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        # Feeding a ZIP to a CSV reader produces garbage rows rather than an
        # error the seller can act on.
        session = await signed_in_shop(client, unique_phone)
        response = await client.post(
            "/v1/imports",
            files={"file": ("book.xlsx", b"PK\x03\x04rest", "application/vnd.ms-excel")},
            data={"template": "PRODUCTS"},
            headers=auth_header(session),
        )
        assert response.status_code == 422
        assert "CSV" in response.json()["message_en"]

    async def test_a_seller_supplied_mapping_overrides_detection(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        batch = await upload(
            client,
            session,
            template="PRODUCTS",
            content="ColA,ColB\nWidget,999\n",
            filename="p.csv",
        )
        response = await client.post(
            f"/v1/imports/{batch['id']}/dry-run",
            json={"column_mapping": {"name": "ColA", "price": "ColB"}},
            headers=auth_header(session),
        )
        assert response.status_code == 200
        assert response.json()["ready_count"] == 1


class TestImportIdempotency:
    async def test_the_identical_file_cannot_be_committed_twice(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        """Master spec section 98: idempotent duplicate handling.

        Re-uploading the same export is a common accident, and doubling a
        seller's catalogue is expensive to undo by hand.
        """
        session = await signed_in_shop(client, unique_phone)
        batch = await upload(
            client, session, template="PRODUCTS", content=PRODUCTS_CSV, filename="p.csv"
        )
        await dry_run(client, session, batch["id"])
        await client.post(f"/v1/imports/{batch['id']}/commit", headers=auth_header(session))

        response = await client.post(
            "/v1/imports",
            files={"file": ("p-again.csv", PRODUCTS_CSV.encode(), "text/csv")},
            data={"template": "PRODUCTS"},
            headers=auth_header(session),
        )
        assert response.status_code == 409
        assert response.json()["details"]["created_count"] == 3

    async def test_a_repeated_row_inside_one_file_is_flagged(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        csv_text = "Product Name,SKU,Selling Price\nAbaya,ABA-1,1250\nAbaya,ABA-1,1250\n"
        batch = await upload(
            client, session, template="PRODUCTS", content=csv_text, filename="p.csv"
        )
        report = await dry_run(client, session, batch["id"])

        assert report["ready_count"] == 1
        assert report["duplicate_count"] == 1

    async def test_committing_twice_is_refused(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        batch = await upload(
            client, session, template="PRODUCTS", content=PRODUCTS_CSV, filename="p.csv"
        )
        await dry_run(client, session, batch["id"])
        await client.post(f"/v1/imports/{batch['id']}/commit", headers=auth_header(session))
        again = await client.post(f"/v1/imports/{batch['id']}/commit", headers=auth_header(session))
        assert again.status_code == 409


class TestImportIsolation:
    async def test_one_shop_cannot_read_anothers_import(self, client: AsyncClient) -> None:
        first = await signed_in_shop(client, "01722200011", shop_name="Shop W")
        second = await signed_in_shop(client, "01722200022", shop_name="Shop X")

        batch = await upload(
            client, first, template="PRODUCTS", content=PRODUCTS_CSV, filename="p.csv"
        )
        response = await client.get(f"/v1/imports/{batch['id']}", headers=auth_header(second))
        assert response.status_code == 404

    async def test_the_same_file_in_two_shops_is_not_a_duplicate(self, client: AsyncClient) -> None:
        # Deduplication is per tenant; two sellers may legitimately import the
        # same supplier catalogue.
        first = await signed_in_shop(client, "01722200033", shop_name="Shop Y")
        second = await signed_in_shop(client, "01722200044", shop_name="Shop Z")

        batch = await upload(
            client, first, template="PRODUCTS", content=PRODUCTS_CSV, filename="p.csv"
        )
        await dry_run(client, first, batch["id"])
        await client.post(f"/v1/imports/{batch['id']}/commit", headers=auth_header(first))

        # The second shop imports the identical file without objection.
        await upload(client, second, template="PRODUCTS", content=PRODUCTS_CSV, filename="p.csv")


class TestSyncPush:
    async def test_a_queued_order_is_created(self, client: AsyncClient, unique_phone: str) -> None:
        session = await signed_in_shop(client, unique_phone)
        entity_id = str(uuid.uuid4())

        response = await client.post(
            "/v1/sync/mutations",
            json={
                "mutations": [
                    {
                        "mutation_id": str(uuid.uuid4()),
                        "entity_type": "ORDER",
                        "entity_id": entity_id,
                        "operation": "CREATE",
                        "payload": {
                            "phone": "01712345678",
                            "customer_name": "Nusrat",
                            "items": [
                                {"name": "Abaya", "quantity": 1, "unit_price_paisa": 125_000}
                            ],
                        },
                    }
                ]
            },
            headers=auth_header(session),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["applied_count"] == 1
        assert body["results"][0]["status"] == "APPLIED"

        orders = await client.get("/v1/orders", headers=auth_header(session))
        assert len(orders.json()["items"]) == 1

    async def test_resending_the_batch_creates_nothing_new(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        """The whole point of the mutation ledger.

        A device on a bad connection resends its queue. Creating a second order
        would mean two parcels booked for one sale.
        """
        session = await signed_in_shop(client, unique_phone)
        batch = {
            "mutations": [
                {
                    "mutation_id": str(uuid.uuid4()),
                    "entity_type": "ORDER",
                    "entity_id": str(uuid.uuid4()),
                    "operation": "CREATE",
                    "payload": {
                        "phone": "01712345678",
                        "items": [{"name": "Abaya", "quantity": 1, "unit_price_paisa": 125_000}],
                    },
                }
            ]
        }

        first = await client.post("/v1/sync/mutations", json=batch, headers=auth_header(session))
        second = await client.post("/v1/sync/mutations", json=batch, headers=auth_header(session))

        assert first.json()["applied_count"] == 1
        assert second.json()["duplicate_count"] == 1
        assert second.json()["results"][0]["entity_id"] == first.json()["results"][0]["entity_id"]

        orders = await client.get("/v1/orders", headers=auth_header(session))
        assert len(orders.json()["items"]) == 1

    async def test_a_stale_edit_is_a_conflict_and_applies_nothing(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        # Master spec section 128: the seller sees both versions and chooses.
        session = await signed_in_shop(client, unique_phone)
        created = await client.post(
            "/v1/orders",
            json={
                "phone": "01712345678",
                "items": [{"name": "Abaya", "quantity": 1, "unit_price_paisa": 125_000}],
            },
            headers=auth_header(session),
        )
        order = created.json()["order"]

        await client.patch(
            f"/v1/orders/{order['id']}",
            json={"note": "Server-side edit"},
            headers=auth_header(session),
        )

        response = await client.post(
            "/v1/sync/mutations",
            json={
                "mutations": [
                    {
                        "mutation_id": str(uuid.uuid4()),
                        "entity_type": "ORDER",
                        "entity_id": order["id"],
                        "operation": "UPDATE",
                        "base_version": 1,
                        "payload": {"note": "Stale offline edit"},
                    }
                ]
            },
            headers=auth_header(session),
        )
        result = response.json()["results"][0]
        assert result["status"] == "CONFLICT"
        assert result["server_state"]["note"] == "Server-side edit"

        unchanged = await client.get(f"/v1/orders/{order['id']}", headers=auth_header(session))
        assert unchanged.json()["note"] == "Server-side edit"

    async def test_a_courier_derived_status_cannot_be_set_from_a_device(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        """Master spec section 37: courier state is server-authoritative.

        An offline device cannot know a parcel was delivered. Accepting this
        would create a COD receivable for money nobody collected.
        """
        session = await signed_in_shop(client, unique_phone)
        created = await client.post(
            "/v1/orders",
            json={
                "phone": "01712345678",
                "items": [{"name": "Abaya", "quantity": 1, "unit_price_paisa": 125_000}],
            },
            headers=auth_header(session),
        )
        order = created.json()["order"]

        response = await client.post(
            "/v1/sync/mutations",
            json={
                "mutations": [
                    {
                        "mutation_id": str(uuid.uuid4()),
                        "entity_type": "ORDER",
                        "entity_id": order["id"],
                        "operation": "UPDATE",
                        "payload": {"status": "COMPLETED"},
                    }
                ]
            },
            headers=auth_header(session),
        )
        result = response.json()["results"][0]
        assert result["status"] == "REJECTED"
        assert "courier outcome" in result["error_message"]

    async def test_an_unsyncable_entity_is_rejected(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        # Booking is not in SyncEntity: queueing it would be pretending an
        # external courier action succeeded offline (section 62.17).
        session = await signed_in_shop(client, unique_phone)
        response = await client.post(
            "/v1/sync/mutations",
            json={
                "mutations": [
                    {
                        "mutation_id": str(uuid.uuid4()),
                        "entity_type": "COURIER_BOOKING",
                        "entity_id": str(uuid.uuid4()),
                        "operation": "CREATE",
                        "payload": {},
                    }
                ]
            },
            headers=auth_header(session),
        )
        result = response.json()["results"][0]
        assert result["status"] == "REJECTED"
        assert "cannot be synced" in result["error_message"]

    async def test_one_bad_mutation_does_not_discard_the_batch(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        response = await client.post(
            "/v1/sync/mutations",
            json={
                "mutations": [
                    {
                        "mutation_id": str(uuid.uuid4()),
                        "entity_type": "ORDER",
                        "entity_id": str(uuid.uuid4()),
                        "operation": "CREATE",
                        "payload": {
                            "phone": "01712345678",
                            "items": [{"name": "Good", "quantity": 1, "unit_price_paisa": 1000}],
                        },
                    },
                    {
                        "mutation_id": str(uuid.uuid4()),
                        "entity_type": "ORDER",
                        "entity_id": str(uuid.uuid4()),
                        "operation": "CREATE",
                        "payload": {"phone": "not-a-phone", "items": []},
                    },
                ]
            },
            headers=auth_header(session),
        )
        body = response.json()
        assert body["applied_count"] == 1
        assert body["rejected_count"] == 1

    async def test_a_queued_stock_adjustment_reaches_the_ledger(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        product = await create_product(client, session, opening_stock=10)

        response = await client.post(
            "/v1/sync/mutations",
            json={
                "mutations": [
                    {
                        "mutation_id": str(uuid.uuid4()),
                        "entity_type": "STOCK_ADJUSTMENT",
                        "entity_id": str(uuid.uuid4()),
                        "operation": "CREATE",
                        "payload": {
                            "product_id": product["id"],
                            "quantity_delta": -4,
                            "note": "Counted offline",
                        },
                    }
                ]
            },
            headers=auth_header(session),
        )
        assert response.json()["applied_count"] == 1

        current = await client.get(f"/v1/products/{product['id']}", headers=auth_header(session))
        assert current.json()["stock_on_hand"] == 6


class TestSyncChanges:
    async def test_changes_are_returned_with_a_cursor(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        await create_product(client, session, name="Synced Product")

        response = await client.get("/v1/sync/changes", headers=auth_header(session))
        assert response.status_code == 200
        body = response.json()
        assert any(entry["entity_type"] == "PRODUCT" for entry in body["changes"])
        assert body["server_time"] is not None

    async def test_an_archived_record_comes_back_as_a_tombstone(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        # Master spec section 38: without a tombstone a device that was offline
        # keeps showing a record the seller deleted.
        session = await signed_in_shop(client, unique_phone)
        product = await create_product(client, session)
        await client.patch(
            f"/v1/products/{product['id']}",
            json={"archived": True},
            headers=auth_header(session),
        )

        response = await client.get("/v1/sync/changes", headers=auth_header(session))
        entry = next(
            item for item in response.json()["changes"] if item["entity_id"] == product["id"]
        )
        assert entry["deleted"] is True

    async def test_changes_never_cross_shops(self, client: AsyncClient) -> None:
        first = await signed_in_shop(client, "01722200055", shop_name="Shop AA")
        second = await signed_in_shop(client, "01722200066", shop_name="Shop AB")

        product = await create_product(client, first, name="Only in AA")

        response = await client.get("/v1/sync/changes", headers=auth_header(second))
        ids = [entry["entity_id"] for entry in response.json()["changes"]]
        assert product["id"] not in ids

    async def test_a_malformed_cursor_is_rejected(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        response = await client.get(
            "/v1/sync/changes?cursor=not-a-cursor", headers=auth_header(session)
        )
        assert response.status_code == 422

    async def test_a_mutation_from_one_shop_cannot_touch_anothers_order(
        self, client: AsyncClient
    ) -> None:
        first = await signed_in_shop(client, "01722200077", shop_name="Shop AC")
        second = await signed_in_shop(client, "01722200088", shop_name="Shop AD")

        created = await client.post(
            "/v1/orders",
            json={
                "phone": "01712345678",
                "items": [{"name": "Abaya", "quantity": 1, "unit_price_paisa": 125_000}],
            },
            headers=auth_header(first),
        )
        order = created.json()["order"]

        response = await client.post(
            "/v1/sync/mutations",
            json={
                "mutations": [
                    {
                        "mutation_id": str(uuid.uuid4()),
                        "entity_type": "ORDER",
                        "entity_id": order["id"],
                        "operation": "UPDATE",
                        "payload": {"note": "cross-tenant attempt"},
                    }
                ]
            },
            headers=auth_header(second),
        )
        assert response.json()["results"][0]["status"] == "REJECTED"

        unchanged = await client.get(f"/v1/orders/{order['id']}", headers=auth_header(first))
        assert unchanged.json()["note"] is None
