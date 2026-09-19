"""Inventory V2: variants, a traceable ledger, explicit return receipt.

What these tests protect:

* existing simple products keep working, and the backfill makes their ledger
  agree with their stock exactly once;
* stock never moves twice for the same thing — a retried request, a re-run
  dispatch, a repeated cancellation, a second receipt of the same return, a
  resumed import;
* a courier saying RETURNED does not restock; only the seller's receipt does;
* a sale cannot quietly take stock below zero, even when requests race;
* one shop can never read or move another shop's stock, and only roles with
  ``inventory.adjust`` can move it at all.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest_commerce import create_order, create_product, signed_in_shop
from tests.test_auth_flow import auth_header
from tests.test_imports_sync import dry_run, upload
from tests.test_team import _member_session

from app.core.clock import utc_now
from app.core.context import RequestContext, set_context
from app.products.models import Product, StockMovement, StockMovementReason
from app.products.service import StockAdjustment, StockService
from app.tenants.roles import TenantRole

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _h(session: dict[str, Any]) -> dict[str, str]:
    return auth_header(session)


async def _product(client: AsyncClient, session: dict, product_id: str) -> dict:
    response = await client.get(f"/v1/products/{product_id}", headers=_h(session))
    assert response.status_code == 200, response.text
    return response.json()


async def _movements(client: AsyncClient, session: dict, product_id: str, **params: Any) -> list:
    response = await client.get(
        f"/v1/products/{product_id}/stock-movements",
        params={"limit": 100, **params},
        headers=_h(session),
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def _variant_product(client: AsyncClient, session: dict, **stocks: int) -> dict:
    """A T-shirt with one variant per keyword: ``black_m=4`` -> "Black / M"."""
    product = await create_product(client, session, name="T-Shirt", opening_stock=0)
    for key, stock in stocks.items():
        name = " / ".join(
            part.capitalize() if len(part) > 1 else part.upper() for part in key.split("_")
        )
        response = await client.post(
            f"/v1/products/{product['id']}/variants",
            json={"name": name, "opening_stock": stock, "low_stock_threshold": 2},
            headers=_h(session),
        )
        assert response.status_code == 201, response.text
        product = response.json()
    return product


def _variant(product: dict, name: str) -> dict:
    return next(v for v in product["variants"] if v["name"] == name)


async def _dispatch(client: AsyncClient, session: dict, order_id: str) -> Any:
    return await client.post(
        f"/v1/consignments/orders/{order_id}/dispatch",
        json={"provider": "manual"},
        headers=_h(session),
    )


async def _returned_parcel(
    client: AsyncClient, session: dict, *, quantity: int = 3, stock: int = 10
) -> dict:
    product = await create_product(client, session, name="Cotton Abaya", opening_stock=stock)
    created = await create_order(
        client,
        session,
        items=[{"product_id": product["id"], "quantity": quantity, "unit_price_paisa": 100_000}],
    )
    dispatched = await _dispatch(client, session, created["order"]["id"])
    assert dispatched.status_code == 201, dispatched.text
    outcome = await client.post(
        f"/v1/consignments/{dispatched.json()['id']}/outcome",
        json={"status": "RETURNED"},
        headers=_h(session),
    )
    assert outcome.status_code == 200, outcome.text
    return {"product": product, "consignment": outcome.json(), "order": created["order"]}


# --------------------------------------------------------------------------- #
# A. existing products, D. backfill
# --------------------------------------------------------------------------- #


class TestSimpleProductsAndBackfill:
    async def test_a_simple_product_is_unchanged(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        product = await create_product(client, session, opening_stock=7, low_stock_threshold=7)
        assert product["has_variants"] is False
        assert product["variants"] == []
        assert product["stock_on_hand"] == 7
        assert product["is_low_stock"] is True

        movement = await client.post(
            f"/v1/products/{product['id']}/stock-adjustments",
            json={"quantity_delta": 3, "note": "found in the back room"},
            headers=_h(session),
        )
        assert movement.status_code == 201, movement.text
        assert (await _product(client, session, product["id"]))["is_low_stock"] is False

    def test_backfill_carries_existing_stock_into_the_ledger_exactly_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Stock that predates the ledger becomes one OPENING movement.

        Run on a scratch database: migrate to the revision before Inventory V2,
        write a product whose stock has no movements behind it, upgrade, then
        go down and up again — the second pass must not add anything.
        """
        from alembic import command
        from alembic.config import Config

        from app.core.config import get_settings, reset_settings_cache
        from app.db.types import JSONColumn, TZDateTime

        database_file = tmp_path / "backfill.db"
        monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_file}")
        reset_settings_cache()
        try:
            config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
            config.set_main_option("sqlalchemy.url", get_settings().database_url)
            command.upgrade(config, "b3f8a1d6c2e7")

            engine = sa.create_engine(f"sqlite:///{database_file}")
            tenants = sa.table(
                "tenants",
                *(
                    sa.column(name, type_)
                    for name, type_ in (
                        ("id", sa.Uuid()),
                        ("name", sa.String()),
                        ("business_category", sa.String()),
                        ("status", sa.String()),
                        ("timezone", sa.String()),
                        ("currency", sa.String()),
                        ("order_number_prefix", sa.String()),
                        ("onboarding_step", sa.String()),
                        ("settings", JSONColumn),
                        ("created_at", TZDateTime()),
                        ("updated_at", TZDateTime()),
                    )
                ),
            )
            products = sa.table(
                "products",
                *(
                    sa.column(name, type_)
                    for name, type_ in (
                        ("id", sa.Uuid()),
                        ("tenant_id", sa.Uuid()),
                        ("name", sa.String()),
                        ("cost_paisa", sa.BigInteger()),
                        ("default_selling_price_paisa", sa.BigInteger()),
                        ("stock_tracking_enabled", sa.Boolean()),
                        ("is_active", sa.Boolean()),
                        ("stock_on_hand", sa.Integer()),
                        ("attributes", JSONColumn),
                        ("created_at", TZDateTime()),
                        ("updated_at", TZDateTime()),
                    )
                ),
            )
            tenant_id, product_id, zero_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            now = utc_now()
            with engine.begin() as conn:
                conn.execute(
                    tenants.insert().values(
                        id=tenant_id,
                        name="Old Shop",
                        business_category="CLOTHING",
                        status="ACTIVE",
                        timezone="Asia/Dhaka",
                        currency="BDT",
                        order_number_prefix="ES",
                        onboarding_step="DONE",
                        settings={},
                        created_at=now,
                        updated_at=now,
                    )
                )
                for pid, stock in ((product_id, 12), (zero_id, 0)):
                    conn.execute(
                        products.insert().values(
                            id=pid,
                            tenant_id=tenant_id,
                            name=f"Legacy {stock}",
                            cost_paisa=0,
                            default_selling_price_paisa=0,
                            stock_tracking_enabled=True,
                            is_active=True,
                            stock_on_hand=stock,
                            attributes={},
                            created_at=now,
                            updated_at=now,
                        )
                    )

            command.upgrade(config, "head")
            command.downgrade(config, "b3f8a1d6c2e7")
            command.upgrade(config, "head")

            with engine.connect() as conn:
                rows = conn.execute(
                    sa.text(
                        "SELECT product_id, quantity_delta, balance_after, reason "
                        "FROM stock_movements"
                    )
                ).all()
                stock = conn.execute(
                    sa.text("SELECT stock_on_hand, has_variants FROM products WHERE name = :n"),
                    {"n": "Legacy 12"},
                ).one()
            engine.dispose()

            # One movement for the stocked product, none for the empty one, and
            # the stock figure itself untouched.
            assert len(rows) == 1
            assert rows[0][1:] == (12, 12, "OPENING")
            assert stock[0] == 12
            assert not stock[1]
        finally:
            monkeypatch.undo()
            reset_settings_cache()


# --------------------------------------------------------------------------- #
# B. variants
# --------------------------------------------------------------------------- #


class TestVariants:
    async def test_variants_hold_their_own_stock_and_the_product_sums_them(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        product = await _variant_product(client, session, black_m=4, black_l=3, white_m=0)

        assert product["has_variants"] is True
        assert [v["name"] for v in product["variants"]] == ["Black / M", "Black / L", "White / M"]
        assert product["stock_on_hand"] == 7
        assert _variant(product, "Black / M")["stock_on_hand"] == 4
        # White / M is at 0 with a threshold of 2, so the product is low.
        assert product["is_low_stock"] is True

        black_m = _variant(product, "Black / M")
        history = await _movements(client, session, product["id"], variant_id=black_m["id"])
        assert [(m["reason"], m["quantity_delta"], m["variant_name"]) for m in history] == [
            ("OPENING", 4, "Black / M")
        ]

    async def test_splitting_a_stocked_product_moves_its_stock_out_explicitly(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        product = await create_product(client, session, name="Kurti", opening_stock=5)
        response = await client.post(
            f"/v1/products/{product['id']}/variants",
            json={"name": "Red / S", "opening_stock": 5},
            headers=_h(session),
        )
        assert response.status_code == 201, response.text
        assert response.json()["stock_on_hand"] == 5

        movements = await _movements(client, session, product["id"])
        assert [(m["reason"], m["quantity_delta"]) for m in movements] == [
            ("OPENING", 5),
            ("MANUAL_ADJUSTMENT", -5),
            ("OPENING", 5),
        ]
        assert movements[1]["note"] == "Stock moved into variants"

    async def test_a_variant_product_movement_must_name_its_variant(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        product = await _variant_product(client, session, black_m=4)
        response = await client.post(
            f"/v1/products/{product['id']}/stock-adjustments",
            json={"quantity_delta": 1, "note": "count"},
            headers=_h(session),
        )
        assert response.status_code == 422

    async def test_variant_sku_is_unique_across_products_and_variants(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        await create_product(client, session, name="Cap", sku="CAP-1")
        product = await _variant_product(client, session, black_m=1)
        response = await client.post(
            f"/v1/products/{product['id']}/variants",
            json={"name": "Grey / M", "sku": "CAP-1"},
            headers=_h(session),
        )
        assert response.status_code == 409


# --------------------------------------------------------------------------- #
# E. order deduction, K. availability
# --------------------------------------------------------------------------- #


class TestOrderDeduction:
    async def test_a_variant_order_deducts_that_variant_once(
        self, client: AsyncClient, unique_phone: str, db: AsyncSession
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        product = await _variant_product(client, session, black_m=4, black_l=3)
        black_l = _variant(product, "Black / L")

        created = await create_order(
            client,
            session,
            items=[{"product_id": product["id"], "variant_id": black_l["id"], "quantity": 2}],
        )
        line = created["order"]["items"][0]
        assert line["variant_id"] == black_l["id"]
        assert line["variant_label"] == "Black / L"

        dispatched = await _dispatch(client, session, created["order"]["id"])
        assert dispatched.status_code == 201, dispatched.text

        after = await _product(client, session, product["id"])
        assert _variant(after, "Black / L")["stock_on_hand"] == 1
        assert _variant(after, "Black / M")["stock_on_hand"] == 4
        assert after["stock_on_hand"] == 5

        sale = [
            m
            for m in await _movements(client, session, product["id"])
            if m["reason"] == "BOOKED_DECREMENT"
        ]
        assert len(sale) == 1
        assert sale[0]["order_number"] == created["order"]["order_number"]

        # A retried fulfilment of the same parcel does not deduct again.
        from app.consignments.service import ConsignmentService
        from app.money.service import ReceivableService
        from app.orders.models import Order

        set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(session["tenant_id"])))
        service = ConsignmentService(db, receivables=ReceivableService(db))
        consignment = await service.get(uuid.UUID(dispatched.json()["id"]))
        order = await db.get(Order, uuid.UUID(created["order"]["id"]))
        await service.fulfil_dispatch(consignment, order=order)
        await db.commit()
        again = await _product(client, session, product["id"])
        assert _variant(again, "Black / L")["stock_on_hand"] == 1

    async def test_an_order_for_a_variant_product_must_choose_a_variant(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        product = await _variant_product(client, session, black_m=4)
        response = await client.post(
            "/v1/orders",
            json={"phone": "01712345678", "items": [{"product_id": product["id"]}]},
            headers=_h(session),
        )
        assert response.status_code == 422

    async def test_dispatch_is_refused_when_stock_is_short_and_nothing_moves(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        product = await create_product(client, session, opening_stock=2)
        created = await create_order(
            client, session, items=[{"product_id": product["id"], "quantity": 3}]
        )
        response = await _dispatch(client, session, created["order"]["id"])
        assert response.status_code == 409
        assert response.json()["code"] == "INSUFFICIENT_STOCK"
        assert response.json()["details"]["stock_on_hand"] == 2

        assert (await _product(client, session, product["id"]))["stock_on_hand"] == 2
        assert len(await _movements(client, session, product["id"])) == 1

    async def test_concurrent_decreases_cannot_oversell(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        product = await _variant_product(client, session, black_m=3)
        variant_id = _variant(product, "Black / M")["id"]

        async def take_one() -> int:
            response = await client.post(
                f"/v1/products/{product['id']}/stock-adjustments",
                json={"quantity_delta": -1, "note": "sold at the stall", "variant_id": variant_id},
                headers=_h(session),
            )
            return response.status_code

        results = await asyncio.gather(*(take_one() for _ in range(5)))
        assert sorted(results) == [201, 201, 201, 409, 409]
        after = await _product(client, session, product["id"])
        assert _variant(after, "Black / M")["stock_on_hand"] == 0
        assert after["stock_on_hand"] == 0


# --------------------------------------------------------------------------- #
# cancellation restore, F. return receipt
# --------------------------------------------------------------------------- #


class TestCancellationAndReturns:
    async def test_a_cancelled_parcel_restores_its_stock_once(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        product = await create_product(client, session, opening_stock=10)
        created = await create_order(
            client, session, items=[{"product_id": product["id"], "quantity": 2}]
        )
        dispatched = (await _dispatch(client, session, created["order"]["id"])).json()
        for _ in range(2):
            await client.post(
                f"/v1/consignments/{dispatched['id']}/outcome",
                json={"status": "CANCELLED"},
                headers=_h(session),
            )

        assert (await _product(client, session, product["id"]))["stock_on_hand"] == 10
        restores = [
            m
            for m in await _movements(client, session, product["id"])
            if m["reason"] == "CANCEL_RESTORE"
        ]
        assert len(restores) == 1

    async def test_a_courier_return_does_not_restock_until_received(
        self, client: AsyncClient, unique_phone: str, db: AsyncSession
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        parcel = await _returned_parcel(client, session)
        product_id = parcel["product"]["id"]
        consignment = parcel["consignment"]

        # RETURNED by the courier: nothing back on the shelf yet, and the
        # order screen knows there is a return waiting.
        assert (await _product(client, session, product_id))["stock_on_hand"] == 7
        assert consignment["items"][0]["qty_return_pending"] == 3
        order_url = f"/v1/orders/{parcel['order']['id']}"
        detail = (await client.get(order_url, headers=_h(session))).json()
        assert detail["consignment_id"] == consignment["id"]
        assert (detail["consignment_status"], detail["return_pending_units"]) == ("RETURNED", 3)

        # The reconciliation scan lists it; receiving it resolves the case.
        from app.reconciliation.models import CaseKind, ReconciliationCase
        from app.reconciliation.service import ReconciliationService

        set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(session["tenant_id"])))
        assert await ReconciliationService(db).scan_for_cases() >= 1
        await db.commit()

        received = await client.post(
            f"/v1/consignments/{consignment['id']}/return-receipt",
            json={"decision": "RESTOCK_ALL", "note": "All three came back sealed"},
            headers=_h(session),
        )
        assert received.status_code == 200, received.text
        line = received.json()["items"][0]
        assert (line["qty_restocked"], line["qty_not_restocked"], line["qty_return_pending"]) == (
            3,
            0,
            0,
        )
        assert (await _product(client, session, product_id))["stock_on_hand"] == 10
        detail = (await client.get(order_url, headers=_h(session))).json()
        assert detail["return_pending_units"] == 0

        # Receiving the same parcel again moves nothing.
        again = await client.post(
            f"/v1/consignments/{consignment['id']}/return-receipt",
            json={"decision": "RESTOCK_ALL"},
            headers=_h(session),
        )
        assert again.status_code == 200
        assert (await _product(client, session, product_id))["stock_on_hand"] == 10
        restocks = [
            m
            for m in await _movements(client, session, product_id)
            if m["reason"] == "RETURN_RESTORE"
        ]
        assert len(restocks) == 1

        # A different decision for a received line is refused, not rewritten.
        changed = await client.post(
            f"/v1/consignments/{consignment['id']}/return-receipt",
            json={
                "decision": "PARTIAL",
                "items": [
                    {"consignment_item_id": line["id"], "qty_restocked": 0, "qty_not_restocked": 3}
                ],
            },
            headers=_h(session),
        )
        assert changed.status_code == 409

        await db.rollback()
        case = (
            await db.execute(
                sa.select(ReconciliationCase).where(
                    ReconciliationCase.kind == str(CaseKind.RETURNED_NOT_RESTOCKED),
                    ReconciliationCase.subject_id == uuid.UUID(consignment["id"]),
                )
            )
        ).scalar_one()
        assert case.status == "RESOLVED"

    async def test_damaged_returns_are_not_restocked_and_count_as_a_loss(
        self, client: AsyncClient, unique_phone: str, db: AsyncSession
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        parcel = await _returned_parcel(client, session)
        received = await client.post(
            f"/v1/consignments/{parcel['consignment']['id']}/return-receipt",
            json={"decision": "RESTOCK_NONE", "note": "Wet and torn"},
            headers=_h(session),
        )
        assert received.status_code == 200, received.text
        assert received.json()["items"][0]["qty_not_restocked"] == 3
        assert (await _product(client, session, parcel["product"]["id"]))["stock_on_hand"] == 7

        from app.profit.service import ProfitService

        set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(session["tenant_id"])))
        snapshot = await ProfitService(db).current_snapshot(uuid.UUID(parcel["consignment"]["id"]))
        assert snapshot is not None
        assert snapshot.write_off_cost_paisa == 3 * 65_000

    async def test_a_partial_restock_needs_every_unit_accounted_for(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        parcel = await _returned_parcel(client, session)
        line_id = parcel["consignment"]["items"][0]["id"]
        url = f"/v1/consignments/{parcel['consignment']['id']}/return-receipt"

        short = await client.post(
            url,
            json={
                "decision": "PARTIAL",
                "items": [
                    {"consignment_item_id": line_id, "qty_restocked": 1, "qty_not_restocked": 1}
                ],
            },
            headers=_h(session),
        )
        assert short.status_code == 422

        ok = await client.post(
            url,
            json={
                "decision": "PARTIAL",
                "items": [
                    {"consignment_item_id": line_id, "qty_restocked": 2, "qty_not_restocked": 1}
                ],
            },
            headers=_h(session),
        )
        assert ok.status_code == 200, ok.text
        assert (await _product(client, session, parcel["product"]["id"]))["stock_on_hand"] == 9


# --------------------------------------------------------------------------- #
# G. manual adjustments, H. restock, I. history
# --------------------------------------------------------------------------- #


class TestAdjustRestockHistory:
    async def test_manual_adjustment_records_actor_and_is_retry_safe(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        product = await create_product(client, session, opening_stock=10)
        body = {
            "quantity_delta": -2,
            "note": "Stock count correction",
            "idempotency_key": "adj-0001-x",
        }

        first = await client.post(
            f"/v1/products/{product['id']}/stock-adjustments", json=body, headers=_h(session)
        )
        second = await client.post(
            f"/v1/products/{product['id']}/stock-adjustments", json=body, headers=_h(session)
        )
        assert first.status_code == second.status_code == 201
        assert first.json()["id"] == second.json()["id"]
        assert first.json()["actor_user_id"] == session["user_id"]
        assert (await _product(client, session, product["id"]))["stock_on_hand"] == 8

        reused = await client.post(
            f"/v1/products/{product['id']}/stock-adjustments",
            json={**body, "quantity_delta": -5},
            headers=_h(session),
        )
        assert reused.status_code == 409
        assert reused.json()["code"] == "IDEMPOTENCY_KEY_CONFLICT"

    async def test_restock_adds_stock_once_and_can_update_cost(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        product = await _variant_product(client, session, black_m=1)
        variant_id = _variant(product, "Black / M")["id"]
        body = {
            "variant_id": variant_id,
            "quantity": 12,
            "unit_cost_paisa": 40_000,
            "update_cost": True,
            "reference": "Invoice 42",
            "note": "Eid batch",
            "idempotency_key": "restock-0042",
        }
        responses = await asyncio.gather(
            *(
                client.post(
                    f"/v1/products/{product['id']}/restocks", json=body, headers=_h(session)
                )
                for _ in range(3)
            )
        )
        assert {r.status_code for r in responses} == {201}
        assert len({r.json()["id"] for r in responses}) == 1
        movement = responses[0].json()
        assert movement["reason"] == "RESTOCK"
        assert movement["reference"] == "Invoice 42"
        assert movement["unit_cost_paisa"] == 40_000

        after = _variant(await _product(client, session, product["id"]), "Black / M")
        assert after["stock_on_hand"] == 13
        assert after["cost_paisa"] == 40_000
        # Above its threshold of 2 again.
        assert after["is_low_stock"] is False

    async def test_history_filters_by_type_and_date_and_pages(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        product = await create_product(client, session, opening_stock=5)
        for index in range(3):
            await client.post(
                f"/v1/products/{product['id']}/restocks",
                json={"quantity": 1 + index},
                headers=_h(session),
            )

        restocks = await _movements(client, session, product["id"], reason="RESTOCK")
        assert [m["quantity_delta"] for m in restocks] == [3, 2, 1]

        future = (utc_now() + timedelta(days=1)).isoformat()
        assert await _movements(client, session, product["id"], occurred_from=future) == []

        page = await client.get(
            f"/v1/products/{product['id']}/stock-movements",
            params={"limit": 2},
            headers=_h(session),
        )
        body = page.json()
        assert len(body["items"]) == 2 and body["has_more"] is True
        rest = await client.get(
            f"/v1/products/{product['id']}/stock-movements",
            params={"limit": 2, "cursor": body["next_cursor"]},
            headers=_h(session),
        )
        assert len(rest.json()["items"]) == 2

    async def test_the_ledger_rebuilds_every_total(
        self, client: AsyncClient, unique_phone: str, db: AsyncSession
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        product = await _variant_product(client, session, black_m=4, black_l=3)
        set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(session["tenant_id"])))
        stock = StockService(db)
        row = await db.get(Product, uuid.UUID(product["id"]))
        assert row is not None
        before = {v.name: v.stock_on_hand for v in row.variants}
        for variant in row.variants:
            variant.stock_on_hand = 999
        row.stock_on_hand = 999
        await stock.recalculate_stock(row.id)
        assert row.stock_on_hand == 7
        assert {v.name: v.stock_on_hand for v in row.variants} == before

    async def test_the_same_key_never_moves_stock_twice_in_the_service(
        self, client: AsyncClient, unique_phone: str, db: AsyncSession
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        product = await create_product(client, session, opening_stock=5)
        set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(session["tenant_id"])))
        stock = StockService(db)
        adjustment = StockAdjustment(
            product_id=uuid.UUID(product["id"]),
            quantity_delta=-1,
            reason=StockMovementReason.BOOKED_DECREMENT,
            idempotency_key="sale:test:line",
        )
        first = await stock.record_movement(adjustment)
        second = await stock.record_movement(adjustment)
        await db.commit()
        assert first.id == second.id
        count = await db.scalar(
            sa.select(sa.func.count(StockMovement.id)).where(
                StockMovement.product_id == uuid.UUID(product["id"])
            )
        )
        assert count == 2  # opening + one sale


# --------------------------------------------------------------------------- #
# J. low stock
# --------------------------------------------------------------------------- #


class TestLowStock:
    async def test_variant_thresholds_feed_the_list_and_the_existing_alert(
        self, client: AsyncClient, unique_phone: str, db: AsyncSession
    ) -> None:
        from app.notifications.smart import SmartAlerts

        session = await signed_in_shop(client, unique_phone)
        product = await _variant_product(client, session, black_m=1, black_l=9)
        await create_product(
            client, session, name="Healthy", opening_stock=50, low_stock_threshold=2
        )

        low = await client.get("/v1/products", params={"low_stock_only": True}, headers=_h(session))
        assert [p["name"] for p in low.json()["items"]] == ["T-Shirt"]

        summary = await client.get("/v1/products/stock-summary", headers=_h(session))
        assert summary.json() == {
            "tracked_products": 2,
            "total_units": 60,
            "low_stock_items": 1,
            "out_of_stock_items": 0,
        }

        set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(session["tenant_id"])))
        [condition] = await SmartAlerts(db).low_stock()
        assert condition.params["names"] == ["T-Shirt (Black / M)"]
        assert condition.params["single"]["stock"] == 1

        await client.post(
            f"/v1/products/{product['id']}/restocks",
            json={"variant_id": _variant(product, "Black / M")["id"], "quantity": 5},
            headers=_h(session),
        )
        await db.rollback()
        assert await SmartAlerts(db).low_stock() == []


# --------------------------------------------------------------------------- #
# L. imports
# --------------------------------------------------------------------------- #


class TestImports:
    async def test_variant_rows_and_a_later_stock_count(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        first = (
            "Product Name,Variant,SKU,Stock,Low Stock\n"
            "Polo,Navy / M,POLO-NM,6,2\n"
            "Polo,Navy / L,POLO-NL,4,2\n"
        )
        batch = await upload(client, session, template="PRODUCTS", content=first, filename="a.csv")
        assert (await dry_run(client, session, batch["id"]))["ready_count"] == 2
        committed = await client.post(f"/v1/imports/{batch['id']}/commit", headers=_h(session))
        assert committed.status_code == 200, committed.text

        [polo] = (await client.get("/v1/products", headers=_h(session))).json()["items"]
        assert polo["stock_on_hand"] == 10
        assert _variant(polo, "Navy / M")["low_stock_threshold"] == 2

        # A stock take: same SKUs, one new count. Only the changed row imports,
        # and it records the difference rather than overwriting history.
        count = "Product Name,Variant,SKU,Stock\nPolo,Navy / M,POLO-NM,2\nPolo,Navy / L,POLO-NL,4\n"
        batch = await upload(client, session, template="PRODUCTS", content=count, filename="b.csv")
        report = await dry_run(client, session, batch["id"])
        assert report["ready_count"] == 1 and report["duplicate_count"] == 1
        await client.post(f"/v1/imports/{batch['id']}/commit", headers=_h(session))
        again = await client.post(f"/v1/imports/{batch['id']}/commit", headers=_h(session))
        assert again.status_code == 409

        polo = await _product(client, session, polo["id"])
        assert _variant(polo, "Navy / M")["stock_on_hand"] == 2
        adjustments = await _movements(client, session, polo["id"], reason="IMPORT_ADJUSTMENT")
        assert [m["quantity_delta"] for m in adjustments] == [-4]


# --------------------------------------------------------------------------- #
# R. isolation and RBAC
# --------------------------------------------------------------------------- #


class TestIsolationAndRoles:
    async def test_one_shop_cannot_read_or_move_anothers_stock(self, client: AsyncClient) -> None:
        shop_a = await signed_in_shop(client, "01766600011", shop_name="Shop A")
        shop_b = await signed_in_shop(client, "01766600022", shop_name="Shop B")
        product = await _variant_product(client, shop_a, black_m=4)
        variant_id = _variant(product, "Black / M")["id"]
        parcel = await _returned_parcel(client, shop_a)

        for method, url, body in (
            ("get", f"/v1/products/{product['id']}/stock-movements", None),
            (
                "post",
                f"/v1/products/{product['id']}/stock-adjustments",
                {"quantity_delta": -1, "note": "x", "variant_id": variant_id},
            ),
            (
                "post",
                f"/v1/products/{product['id']}/restocks",
                {"quantity": 5, "variant_id": variant_id},
            ),
            ("post", f"/v1/products/{product['id']}/variants", {"name": "Stolen"}),
            (
                "post",
                f"/v1/consignments/{parcel['consignment']['id']}/return-receipt",
                {"decision": "RESTOCK_ALL"},
            ),
        ):
            response = await getattr(client, method)(
                url, **({"json": body} if body else {}), headers=_h(shop_b)
            )
            assert response.status_code == 404, (url, response.text)

        after = await _product(client, shop_a, product["id"])
        assert _variant(after, "Black / M")["stock_on_hand"] == 4
        assert (await _product(client, shop_a, parcel["product"]["id"]))["stock_on_hand"] == 7

    async def test_only_inventory_roles_can_move_stock(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        owner = await signed_in_shop(client, unique_phone, shop_name="Role Shop", plan="pro")
        product = await create_product(client, owner, opening_stock=5)
        parcel = await _returned_parcel(client, owner)
        viewer = await _member_session(client, owner, "01744400031", TenantRole.VIEWER)
        operator = await _member_session(client, owner, "01744400032", TenantRole.ORDER_OPERATOR)
        manager = await _member_session(client, owner, "01744400033", TenantRole.MANAGER)

        for member in (viewer, operator):
            assert (
                await client.get(
                    f"/v1/products/{product['id']}/stock-movements", headers=_h(member)
                )
            ).status_code == 200
            for url, body in (
                (
                    f"/v1/products/{product['id']}/stock-adjustments",
                    {"quantity_delta": 1, "note": "x"},
                ),
                (f"/v1/products/{product['id']}/restocks", {"quantity": 1}),
                (
                    f"/v1/consignments/{parcel['consignment']['id']}/return-receipt",
                    {"decision": "RESTOCK_ALL"},
                ),
            ):
                response = await client.post(url, json=body, headers=_h(member))
                assert response.status_code == 403, (url, response.text)

        restocked = await client.post(
            f"/v1/products/{product['id']}/restocks", json={"quantity": 2}, headers=_h(manager)
        )
        assert restocked.status_code == 201
        assert restocked.json()["actor_user_id"] == manager["user_id"]
        assert (await _product(client, owner, product["id"]))["stock_on_hand"] == 7
