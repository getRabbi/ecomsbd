"""The courier worker jobs, run the way the worker runs them.

Every courier test elsewhere drives the services from a request, inside a
tenant-scoped session. The worker reaches the same services from
``app.couriers.jobs``, once per shop, and nothing exercised that path: on
2026-10-01 production's first payment sync for a connected Steadfast account
failed inserting a sync cursor with no tenant, because the job ran every shop
inside one unscoped system session. These tests run the real job functions for
two shops at once.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient

from app.couriers import jobs
from app.couriers.jobs import poll_courier_statuses, sync_courier_payments
from app.couriers.models import CourierSyncCursor, ProviderPayment
from tests.conftest_commerce import signed_in_shop
from tests.fixtures.steadfast import bodies
from tests.test_auth_flow import auth_header

pytestmark = pytest.mark.usefixtures("courier_flags_on")

NO_PAYMENTS = json.dumps({"data": []})


def _only_these_shops(monkeypatch: pytest.MonkeyPatch, shops: set[uuid.UUID]) -> None:
    """Limit the job's cross-shop account listing to this test's shops.

    The test database lives for the whole session, so shops other tests
    connected are still there; everything after the listing runs for real.
    """
    listing = jobs._connected_accounts

    async def only_these(session: Any, provider: str = jobs.PROVIDER) -> Any:
        return [row for row in await listing(session, provider) if row[0] in shops]

    monkeypatch.setattr(jobs, "_connected_accounts", only_these)


async def _connect_steadfast(client: AsyncClient, shop: dict[str, Any], transport: Any) -> None:
    transport.enqueue("GET", "/get_balance", body=bodies.BALANCE_OK)
    response = await client.post(
        "/v1/couriers/accounts/steadfast/connect",
        headers=auth_header(shop),
        json={"credentials": {"api_key": "sfk-jobs-key-abcd", "secret_key": "sfs-jobs-secret"}},
    )
    assert response.status_code == 201, response.text


async def test_payment_sync_runs_each_shop_in_its_own_scope(
    client: AsyncClient, system_db, steadfast_transport, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = await signed_in_shop(client, "01712900001", shop_name="Jobs Shop One")
    second = await signed_in_shop(client, "01712900002", shop_name="Jobs Shop Two")
    await _connect_steadfast(client, first, steadfast_transport)
    await _connect_steadfast(client, second, steadfast_transport)
    shops = {uuid.UUID(first["tenant_id"]), uuid.UUID(second["tenant_id"])}
    _only_these_shops(monkeypatch, shops)

    # Shops are visited in the order the accounts query returns them, so both
    # get the same script: one payment list, then an empty one.
    steadfast_transport.enqueue("GET", "/payments", body=bodies.PAYMENTS_LIST)
    steadfast_transport.enqueue("GET", "/payments", body=NO_PAYMENTS)
    for payment_id in ("55001", "55002"):
        steadfast_transport.enqueue(
            "GET", f"/payments/{payment_id}", body=bodies.PAYMENT_DETAIL_WITH_CONSIGNMENTS
        )

    totals = await sync_courier_payments()

    assert totals["errors"] == 0, totals
    assert totals["tenants"] == 2

    cursors = (
        await system_db.execute(
            sa.select(CourierSyncCursor.tenant_id).where(CourierSyncCursor.tenant_id.in_(shops))
        )
    ).scalars()
    assert sorted(cursors) == sorted(shops), "one cursor per shop, each stamped with its shop"

    owners = set(
        (
            await system_db.execute(
                sa.select(ProviderPayment.tenant_id).where(ProviderPayment.tenant_id.in_(shops))
            )
        ).scalars()
    )
    assert len(owners) == 1, "the imported payments belong to the one shop that was paid"


async def test_status_polling_visits_every_shop_without_errors(
    client: AsyncClient, steadfast_transport, monkeypatch: pytest.MonkeyPatch
) -> None:
    shops: set[uuid.UUID] = set()
    for index in range(2):
        shop = await signed_in_shop(client, f"0171290001{index}", shop_name=f"Poll Shop {index}")
        await _connect_steadfast(client, shop, steadfast_transport)
        shops.add(uuid.UUID(shop["tenant_id"]))
    _only_these_shops(monkeypatch, shops)

    totals = await poll_courier_statuses()

    assert totals.get("errors", 0) == 0, totals
    assert totals.get("tenants", 0) >= 2, totals
