"""The order list carries the courier its parcel is with.

Module 3 asked order views to show a courier name; the list response had none,
so the web table would have had to either omit the column or invent it. This is
the backend half.

The enrichment is a second bounded query rather than a join, so the list query —
the hottest read in the product — is unchanged.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
import sqlalchemy as sa

from app.api.v1.orders import _courier_for_orders
from app.consignments.models import Consignment, ConsignmentStatus
from app.core.clock import utc_now
from app.core.context import RequestContext, set_context
from app.orders.models import Order, OrderChannel, OrderStatus
from app.tenants.models import Tenant


async def _shop(db) -> Tenant:
    tenant = Tenant(name="Courier Column Shop")
    db.add(tenant)
    await db.flush()
    set_context(RequestContext(trace_id="test", tenant_id=tenant.id))
    return tenant


async def _order(db, number: str) -> Order:
    order = Order(
        order_number=number,
        # The idempotency key the client sends; required, and unique per order.
        client_id=uuid.uuid4(),
        status=str(OrderStatus.CONFIRMED),
        channel=str(OrderChannel.MANUAL),
        business_date=utc_now().date(),
        subtotal_paisa=105000,
        discount_paisa=0,
        delivery_fee_paisa=0,
        cod_amount_paisa=105000,
    )
    db.add(order)
    await db.flush()
    return order


@pytest.mark.asyncio
async def test_an_unbooked_order_reports_no_courier(db) -> None:
    """Absence is a fact, not a missing value: it renders as "not booked"."""
    await _shop(db)
    order = await _order(db, "CP-20260918-0001")

    found = await _courier_for_orders(db, [order.id])

    assert found == {}


@pytest.mark.asyncio
async def test_a_booked_order_reports_its_courier_and_tracking(db) -> None:
    await _shop(db)
    order = await _order(db, "CP-20260918-0002")
    db.add(
        Consignment(
            order_id=order.id,
            provider="pathao",
            merchant_reference="CP-20260918-0002",
            status=str(ConsignmentStatus.BOOKED),
            tracking_code="DA200101",
            cod_amount_paisa=105000,
        )
    )
    await db.flush()

    found = await _courier_for_orders(db, [order.id])

    assert found[order.id] == ("pathao", "DA200101")


@pytest.mark.asyncio
async def test_a_reshipped_order_reports_the_newest_parcel(db) -> None:
    """The one a seller means when they ask where it is."""
    await _shop(db)
    order = await _order(db, "CP-20260918-0003")
    db.add(
        Consignment(
            order_id=order.id,
            provider="steadfast",
            merchant_reference="CP-20260918-0003",
            status=str(ConsignmentStatus.CANCELLED),
            tracking_code="OLD-1",
            cod_amount_paisa=105000,
            created_at=utc_now(),
        )
    )
    await db.flush()
    db.add(
        Consignment(
            order_id=order.id,
            provider="pathao",
            merchant_reference="CP-20260918-0003-2",
            status=str(ConsignmentStatus.BOOKED),
            tracking_code="NEW-2",
            cod_amount_paisa=105000,
            created_at=utc_now() + dt.timedelta(minutes=5),
        )
    )
    await db.flush()

    found = await _courier_for_orders(db, [order.id])

    assert found[order.id] == ("pathao", "NEW-2")


@pytest.mark.asyncio
async def test_a_whole_page_costs_one_query_not_one_per_row(db) -> None:
    """Fifty orders must not become fifty round trips."""
    await _shop(db)
    orders = []
    for index in range(5):
        order = await _order(db, f"CP-20260918-01{index:02d}")
        db.add(
            Consignment(
                order_id=order.id,
                provider="steadfast",
                merchant_reference=order.order_number,
                status=str(ConsignmentStatus.BOOKED),
                cod_amount_paisa=105000,
            )
        )
        orders.append(order)
    await db.flush()

    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    sa.event.listen(db.bind.sync_engine, "before_cursor_execute", record)
    try:
        found = await _courier_for_orders(db, [order.id for order in orders])
    finally:
        sa.event.remove(db.bind.sync_engine, "before_cursor_execute", record)

    assert len(found) == 5
    selects = [item for item in statements if item.strip().upper().startswith("SELECT")]
    assert len(selects) == 1, selects


@pytest.mark.asyncio
async def test_an_empty_page_asks_the_database_nothing(db) -> None:
    assert await _courier_for_orders(db, []) == {}
