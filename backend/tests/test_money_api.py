"""The money endpoints, over HTTP.

Master spec section 39. These exercise the whole flow the Flutter app will
drive — dispatch, deliver, import a statement, reconcile, work a case — through
the real API, so a contract change shows up here rather than on a device.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from tests.conftest_commerce import create_order, create_product, signed_in_shop
from tests.test_auth_flow import auth_header

STATEMENT_HEADER = "Invoice,Amount"


@pytest.fixture
async def shop(client: AsyncClient, unique_phone: str) -> dict[str, Any]:
    return await signed_in_shop(client, unique_phone, shop_name="API Money Shop", plan="pro")


async def _delivered_parcel(
    client: AsyncClient, shop: dict[str, Any], *, cod_paisa: int = 140_500
) -> dict[str, Any]:
    """An order dispatched and delivered, entirely over HTTP."""
    product = await create_product(client, shop, name="Cotton Abaya", opening_stock=10)
    created = await create_order(
        client,
        shop,
        items=[
            {
                "product_id": product["id"],
                "quantity": 1,
                "unit_price_paisa": cod_paisa,
            }
        ],
        cod_amount_paisa=cod_paisa,
    )
    order = created["order"]

    dispatch = await client.post(
        f"/v1/consignments/orders/{order['id']}/dispatch",
        json={"provider": "manual"},
        headers=auth_header(shop),
    )
    assert dispatch.status_code == 201, dispatch.text
    consignment = dispatch.json()

    outcome = await client.post(
        f"/v1/consignments/{consignment['id']}/outcome",
        json={"status": "DELIVERED"},
        headers=auth_header(shop),
    )
    assert outcome.status_code == 200, outcome.text
    return {"order": order, "consignment": outcome.json()}


async def _import_statement(
    client: AsyncClient, shop: dict[str, Any], rows: list[str], *, filename: str
) -> dict[str, Any]:
    body = (STATEMENT_HEADER + "\n" + "\n".join(rows) + "\n").encode()
    response = await client.post(
        "/v1/payouts/import",
        files={"file": (filename, body, "text/csv")},
        data={"provider": "manual"},
        headers=auth_header(shop),
    )
    assert response.status_code == 201, response.text
    return response.json()


class TestConsignments:
    async def test_dispatch_and_delivery_over_http(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        parcel = await _delivered_parcel(client, shop, cod_paisa=140_500)
        consignment = parcel["consignment"]

        assert consignment["status"] == "DELIVERED"
        assert consignment["collectible_paisa"] == 140_500
        assert consignment["delivered_at"] is not None

    async def test_a_partial_delivery_needs_quantities(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        product = await create_product(client, shop, opening_stock=10)
        created = await create_order(
            client,
            shop,
            items=[{"product_id": product["id"], "quantity": 3, "unit_price_paisa": 100_000}],
            cod_amount_paisa=300_000,
        )
        dispatch = await client.post(
            f"/v1/consignments/orders/{created['order']['id']}/dispatch",
            json={},
            headers=auth_header(shop),
        )
        consignment_id = dispatch.json()["id"]

        # Section 17.8: assuming the original COD for a partial is the error
        # this refusal exists to prevent.
        response = await client.post(
            f"/v1/consignments/{consignment_id}/outcome",
            json={"status": "PARTIAL_DELIVERED"},
            headers=auth_header(shop),
        )
        assert response.status_code == 422
        assert response.json()["code"] == "VALIDATION_ERROR"

    async def test_dispatching_contacts_no_courier(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        parcel = await _delivered_parcel(client, shop)
        # Manual mode: there is no provider id because nobody was asked for one.
        assert parcel["consignment"]["provider"] == "manual"
        assert parcel["consignment"]["provider_consignment_id"] is None


class TestMoneySummary:
    async def test_a_delivered_parcel_shows_as_outstanding(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        await _delivered_parcel(client, shop, cod_paisa=140_500)

        response = await client.get("/v1/money/summary", headers=auth_header(shop))
        assert response.status_code == 200, response.text
        summary = response.json()

        assert summary["outstanding_paisa"] == 140_500
        # Delivered is not paid (section 1.4).
        assert summary["settled_paisa"] == 0
        assert summary["unpaid_parcel_count"] == 1

    async def test_deductions_are_reported_by_kind(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        response = await client.get("/v1/money/summary", headers=auth_header(shop))
        summary = response.json()
        # Section 84: a single "fees" total would fold an unknown deduction
        # into a familiar category. Each has its own field.
        for field in (
            "courier_charge_paisa",
            "cod_fee_paisa",
            "return_charge_paisa",
            "unknown_deduction_paisa",
            "write_off_paisa",
        ):
            assert field in summary

    async def test_aging_bands_are_returned(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        await _delivered_parcel(client, shop, cod_paisa=140_500)

        response = await client.get("/v1/money/aging", headers=auth_header(shop))
        assert response.status_code == 200
        bands = response.json()
        assert [band["label"] for band in bands] == [
            "0-3 days",
            "4-7 days",
            "8-14 days",
            "15-30 days",
            "30+ days",
        ]
        assert bands[0]["outstanding_paisa"] == 140_500

    async def test_an_empty_shop_reports_zero_not_an_error(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        response = await client.get("/v1/money/summary", headers=auth_header(shop))
        assert response.status_code == 200
        assert response.json()["outstanding_paisa"] == 0


class TestReceivables:
    async def test_receivables_are_listed_with_their_order_number(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        parcel = await _delivered_parcel(client, shop, cod_paisa=140_500)

        response = await client.get(
            "/v1/money/receivables?open_only=true", headers=auth_header(shop)
        )
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["outstanding_paisa"] == 140_500
        assert items[0]["order_number"] == parcel["order"]["order_number"]
        assert items[0]["age_days"] == 0

    async def test_a_parcel_in_transit_is_not_open_money(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        product = await create_product(client, shop, opening_stock=5)
        created = await create_order(
            client,
            shop,
            items=[{"product_id": product["id"], "quantity": 1, "unit_price_paisa": 100_000}],
            cod_amount_paisa=100_000,
        )
        await client.post(
            f"/v1/consignments/orders/{created['order']['id']}/dispatch",
            json={},
            headers=auth_header(shop),
        )

        response = await client.get(
            "/v1/money/receivables?open_only=true", headers=auth_header(shop)
        )
        # Nobody owes anything until the parcel is delivered.
        assert response.json()["items"] == []

    async def test_the_ledger_explains_a_receivable(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        await _delivered_parcel(client, shop, cod_paisa=140_500)
        listing = await client.get("/v1/money/receivables", headers=auth_header(shop))
        receivable_id = listing.json()["items"][0]["id"]

        response = await client.get(
            f"/v1/money/receivables/{receivable_id}/ledger", headers=auth_header(shop)
        )
        assert response.status_code == 200, response.text
        entries = response.json()
        # The answer to "why does this say ৳1,405 outstanding?".
        assert entries[0]["event_type"] == "DELIVERY_CONFIRMED"
        assert entries[0]["amount_paisa"] == 140_500
        assert entries[0]["bucket"] == "COD_RECEIVABLE"

    async def test_a_correction_states_a_change_and_needs_a_reason(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        await _delivered_parcel(client, shop, cod_paisa=140_500)
        listing = await client.get("/v1/money/receivables", headers=auth_header(shop))
        receivable_id = listing.json()["items"][0]["id"]

        # Section 134: no "new balance" field exists, and the reason is not
        # optional.
        rejected = await client.post(
            f"/v1/money/receivables/{receivable_id}/correction",
            json={"amount_paisa": 5_000, "increases_balance": False},
            headers=auth_header(shop),
        )
        assert rejected.status_code == 422

        response = await client.post(
            f"/v1/money/receivables/{receivable_id}/correction",
            json={
                "amount_paisa": 5_000,
                "increases_balance": False,
                "reason": "Courier confirmed a ৳50 discount at the door",
            },
            headers=auth_header(shop),
        )
        assert response.status_code == 200, response.text
        assert response.json()["outstanding_paisa"] == 135_500

    async def test_a_write_off_records_the_loss(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        await _delivered_parcel(client, shop, cod_paisa=140_500)
        listing = await client.get("/v1/money/receivables", headers=auth_header(shop))
        receivable_id = listing.json()["items"][0]["id"]

        response = await client.post(
            f"/v1/money/receivables/{receivable_id}/write-off",
            json={"reason": "Courier stopped trading"},
            headers=auth_header(shop),
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "WRITTEN_OFF"

        summary = await client.get("/v1/money/summary", headers=auth_header(shop))
        # Gone from outstanding, but visible as a loss rather than vanished.
        assert summary.json()["outstanding_paisa"] == 0
        assert summary.json()["write_off_paisa"] == 140_500


class TestPayoutEndpoints:
    async def test_a_manual_payout_is_recorded_unmatched(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        response = await client.post(
            "/v1/payouts/manual",
            json={"provider": "manual", "total_paisa": 480_500},
            headers=auth_header(shop),
        )
        assert response.status_code == 201, response.text
        payout = response.json()
        assert payout["total_paisa"] == 480_500
        assert payout["unexplained_paisa"] == 480_500
        assert payout["lines"][0]["status"] == "UNMATCHED"

    async def test_a_preview_saves_nothing(self, client: AsyncClient, shop: dict[str, Any]) -> None:
        body = b"Invoice,Amount\nCP-1,1405.00\nCP-2,pending\n"
        response = await client.post(
            "/v1/payouts/preview",
            files={"file": ("stmt.csv", body, "text/csv")},
            headers=auth_header(shop),
        )
        assert response.status_code == 200, response.text
        preview = response.json()
        assert preview["row_count"] == 2
        assert preview["invalid_row_count"] == 1
        # The seller sees exactly what the file said about the bad row.
        assert "pending" in preview["rows"][1]["errors"][0]

        listing = await client.get("/v1/payouts", headers=auth_header(shop))
        assert listing.json()["items"] == []

    async def test_an_imported_statement_becomes_lines(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        parcel = await _delivered_parcel(client, shop, cod_paisa=140_500)
        reference = parcel["consignment"]["merchant_reference"]
        payout = await _import_statement(client, shop, [f"{reference},1405.00"], filename="a.csv")

        assert payout["total_paisa"] == 140_500
        assert len(payout["lines"]) == 1
        assert payout["lines"][0]["merchant_reference"] == reference

    async def test_re_importing_the_same_file_is_refused(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        await _import_statement(client, shop, ["CP-1,1405.00"], filename="a.csv")
        body = b"Invoice,Amount\nCP-1,1405.00\n"
        response = await client.post(
            "/v1/payouts/import",
            files={"file": ("a-again.csv", body, "text/csv")},
            data={"provider": "manual"},
            headers=auth_header(shop),
        )
        assert response.status_code == 409
        assert response.json()["code"] == "CONFLICT"


class TestReconciliationEndpoints:
    async def test_reconciling_settles_an_exact_reference(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        parcel = await _delivered_parcel(client, shop, cod_paisa=140_500)
        reference = parcel["consignment"]["merchant_reference"]
        payout = await _import_statement(client, shop, [f"{reference},1405.00"], filename="a.csv")

        response = await client.post(
            f"/v1/reconciliation/payouts/{payout['id']}/reconcile",
            headers=auth_header(shop),
        )
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["exact_matches"] == 1
        assert report["applied_paisa"] == 140_500

        summary = await client.get("/v1/money/summary", headers=auth_header(shop))
        assert summary.json()["outstanding_paisa"] == 0
        assert summary.json()["settled_paisa"] == 140_500

    async def test_shadow_mode_reports_without_settling(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        parcel = await _delivered_parcel(client, shop, cod_paisa=140_500)
        reference = parcel["consignment"]["merchant_reference"]
        payout = await _import_statement(client, shop, [f"{reference},1405.00"], filename="a.csv")

        response = await client.post(
            f"/v1/reconciliation/payouts/{payout['id']}/reconcile?shadow=true",
            headers=auth_header(shop),
        )
        report = response.json()
        assert report["shadow"] is True
        assert report["exact_matches"] == 1
        assert report["applied_paisa"] == 0

        summary = await client.get("/v1/money/summary", headers=auth_header(shop))
        # Section 112: measure the rule, move nothing.
        assert summary.json()["outstanding_paisa"] == 140_500

    async def test_an_underpayment_produces_a_case(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        parcel = await _delivered_parcel(client, shop, cod_paisa=140_500)
        reference = parcel["consignment"]["merchant_reference"]
        payout = await _import_statement(client, shop, [f"{reference},1000.00"], filename="a.csv")
        await client.post(
            f"/v1/reconciliation/payouts/{payout['id']}/reconcile",
            headers=auth_header(shop),
        )

        response = await client.get(
            "/v1/reconciliation/cases?kind=UNDERPAID", headers=auth_header(shop)
        )
        assert response.status_code == 200, response.text
        cases = response.json()["items"]
        assert len(cases) == 1
        assert cases[0]["amount_paisa"] == 40_500
        assert cases[0]["status"] == "OPEN"

    async def test_a_manual_match_needs_a_reason(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        await _delivered_parcel(client, shop, cod_paisa=140_500)
        listing = await client.get("/v1/money/receivables", headers=auth_header(shop))
        receivable_id = listing.json()["items"][0]["id"]

        payout = await client.post(
            "/v1/payouts/manual",
            json={"provider": "manual", "total_paisa": 140_500},
            headers=auth_header(shop),
        )
        line_id = payout.json()["lines"][0]["id"]

        rejected = await client.post(
            f"/v1/reconciliation/lines/{line_id}/match",
            json={"receivable_id": receivable_id},
            headers=auth_header(shop),
        )
        assert rejected.status_code == 422

        response = await client.post(
            f"/v1/reconciliation/lines/{line_id}/match",
            json={
                "receivable_id": receivable_id,
                "reason": "Courier confirmed by phone",
            },
            headers=auth_header(shop),
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "MANUAL_MATCHED"
        assert response.json()["match_reason"] == "Courier confirmed by phone"

    async def test_unmatching_restores_the_receivable(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        parcel = await _delivered_parcel(client, shop, cod_paisa=140_500)
        reference = parcel["consignment"]["merchant_reference"]
        payout = await _import_statement(client, shop, [f"{reference},1405.00"], filename="a.csv")
        await client.post(
            f"/v1/reconciliation/payouts/{payout['id']}/reconcile",
            headers=auth_header(shop),
        )

        detail = await client.get(f"/v1/payouts/{payout['id']}", headers=auth_header(shop))
        line_id = detail.json()["lines"][0]["id"]

        response = await client.post(
            f"/v1/reconciliation/lines/{line_id}/unmatch",
            json={"reason": "Matched the wrong parcel"},
            headers=auth_header(shop),
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "REVERSED"

        summary = await client.get("/v1/money/summary", headers=auth_header(shop))
        assert summary.json()["outstanding_paisa"] == 140_500
        assert summary.json()["settled_paisa"] == 0

    async def test_a_case_cannot_be_closed_without_a_note(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        parcel = await _delivered_parcel(client, shop, cod_paisa=140_500)
        reference = parcel["consignment"]["merchant_reference"]
        payout = await _import_statement(client, shop, [f"{reference},1000.00"], filename="a.csv")
        await client.post(
            f"/v1/reconciliation/payouts/{payout['id']}/reconcile",
            headers=auth_header(shop),
        )
        cases = await client.get("/v1/reconciliation/cases", headers=auth_header(shop))
        case_id = cases.json()["items"][0]["id"]

        rejected = await client.patch(
            f"/v1/reconciliation/cases/{case_id}",
            json={"status": "RESOLVED"},
            headers=auth_header(shop),
        )
        assert rejected.status_code == 422

        response = await client.patch(
            f"/v1/reconciliation/cases/{case_id}",
            json={"status": "RESOLVED", "resolution": "Courier paid the balance"},
            headers=auth_header(shop),
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "RESOLVED"
        assert response.json()["resolved_at"] is not None

    async def test_the_scan_finds_nothing_in_a_clean_shop(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        await _delivered_parcel(client, shop, cod_paisa=140_500)
        response = await client.post("/v1/reconciliation/scan", headers=auth_header(shop))
        assert response.status_code == 200
        # Delivered today, so nothing is late yet.
        assert response.json()["cases_opened"] == 0


class TestIsolation:
    async def test_another_shops_money_is_invisible(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        first = await signed_in_shop(client, unique_phone, shop_name="Shop One")
        await _delivered_parcel(client, first, cod_paisa=140_500)

        second = await signed_in_shop(client, "01955555555", shop_name="Shop Two")
        summary = await client.get("/v1/money/summary", headers=auth_header(second))
        assert summary.json()["outstanding_paisa"] == 0

        receivables = await client.get("/v1/money/receivables", headers=auth_header(second))
        assert receivables.json()["items"] == []
