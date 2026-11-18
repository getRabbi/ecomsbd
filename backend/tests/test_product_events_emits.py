"""V4 WP0.2: each existing service emits its funnel event (flag on)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.models import BillingProviderKind, TransactionKind, TransactionState
from app.billing.providers.base import VerificationResult
from app.billing.service import BillingService
from app.entitlements.catalog import PlanCode
from app.product_events.models import ProductEvent
from tests.conftest_commerce import create_order, signed_in_shop
from tests.fixtures.steadfast import bodies
from tests.test_auth_flow import auth_header
from tests.test_courier_booking import (  # noqa: F401 - fixture imports
    _order,
    accounts,
    booking,
    registry,
    shop,
    transport,
)

pytestmark = pytest.mark.usefixtures("courier_flags_on", "product_events_on")


async def _names(
    session: AsyncSession, tenant_id: uuid.UUID | str
) -> dict[str, list[ProductEvent]]:
    tenant = uuid.UUID(str(tenant_id))
    rows = (
        (await session.execute(sa.select(ProductEvent).where(ProductEvent.tenant_id == tenant)))
        .scalars()
        .all()
    )
    names: dict[str, list[ProductEvent]] = {}
    for row in rows:
        names.setdefault(row.name, []).append(row)
    return names


async def test_shop_creation_and_orders(client: AsyncClient, system_db: AsyncSession) -> None:
    created = await signed_in_shop(client, "01700000071", shop_name="Funnel Orders")
    await create_order(client, created, phone="01700000072")
    await create_order(client, created, phone="01700000073")

    names = await _names(system_db, created["tenant_id"])

    assert len(names["shop_created"]) == 1
    assert names["shop_created"][0].props == {"category": "CLOTHING"}
    assert names["shop_created"][0].user_id is not None
    assert len(names["first_order"]) == 1  # two orders, one first
    assert len(names["weekly_active"]) == 1


# The fixtures below are the imported ones; pytest injects them by name.
async def test_courier_connect_and_booking(
    client: AsyncClient,
    shop: dict[str, Any],  # noqa: F811
    db: AsyncSession,
    booking: Any,  # noqa: F811
    transport: Any,  # noqa: F811
) -> None:
    order = await _order(client, shop)
    transport.enqueue("POST", "/create_order", body=bodies.create_ok(invoice=order["order_number"]))

    report = await booking.book(uuid.UUID(order["id"]))
    assert report.booked == 1

    names = await _names(db, shop["tenant_id"])
    assert names["first_courier_connected"][0].props == {"provider": "steadfast"}
    assert names["first_booking"][0].props == {"provider": "steadfast"}
    assert len(names["weekly_active"]) == 1


async def test_payout_import_and_reconcile(client: AsyncClient, system_db: AsyncSession) -> None:
    created = await signed_in_shop(client, "01700000074", shop_name="Funnel Money", plan="pro")
    body = b"Invoice,Amount\nCP-404,1405.00\n"
    imported = await client.post(
        "/v1/payouts/import",
        files={"file": ("funnel.csv", body, "text/csv")},
        data={"provider": "manual"},
        headers=auth_header(created),
    )
    assert imported.status_code == 201, imported.text

    reconciled = await client.post(
        f"/v1/reconciliation/payouts/{imported.json()['id']}/reconcile",
        headers=auth_header(created),
    )
    assert reconciled.status_code == 200, reconciled.text

    names = await _names(system_db, created["tenant_id"])
    assert names["first_payout_import"][0].props == {"provider": "manual"}
    assert len(names["first_reconcile"]) == 1


@pytest.mark.parametrize(
    ("kind", "amount", "expected"),
    [
        (TransactionKind.PURCHASE, 49_900, 1),
        (TransactionKind.RENEWAL, 49_900, 1),
        (TransactionKind.CREDIT, 0, 0),
        (TransactionKind.PURCHASE, 0, 0),
    ],
)
async def test_plan_paid_only_for_money_received(
    system_db: AsyncSession, kind: TransactionKind, amount: int, expected: int
) -> None:
    from app.tenants.models import Tenant

    tenant = Tenant(name="Funnel Billing", business_category="CLOTHING")
    system_db.add(tenant)
    await system_db.flush()

    record = BillingService._record_transaction  # the one place every payment lands
    await record(
        SimpleNamespace(_db=system_db),  # type: ignore[arg-type]
        tenant_id=tenant.id,
        subscription=None,
        provider=BillingProviderKind.PLAY,
        plan=PlanCode.PRO,
        kind=kind,
        state=TransactionState.VERIFIED,
        result=VerificationResult.VERIFIED,
        provider_event_id=f"evt-{uuid.uuid4()}",
        amount_paisa=amount,
    )

    names = await _names(system_db, tenant.id)
    assert len(names.get("plan_paid", [])) == expected
    if expected:
        assert names["plan_paid"][0].props == {"plan": "pro", "provider": "play"}
