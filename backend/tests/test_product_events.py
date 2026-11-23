"""V4 WP0.2: server-side product funnel events."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.models import AdminPermission, PlatformAdminRole, admin_permissions_for
from app.common.feature_flags import FeatureFlag, FlagKey, FlagScope
from app.core.context import use_context
from app.product_events.models import (
    FUNNEL_STEPS,
    ONCE_PER_SHOP,
    ProductEvent,
    ProductEventName,
)
from app.product_events.service import ProductEventService, clean_props, iso_week_key
from tests.test_admin import admin_client, admin_header  # noqa: F401 - fixture import


async def _tenant(system_db: AsyncSession, name: str = "Funnel Shop") -> uuid.UUID:
    from app.tenants.models import Tenant

    tenant = Tenant(name=name, business_category="CLOTHING")
    system_db.add(tenant)
    await system_db.flush()
    return tenant.id


async def _flag_on(system_db: AsyncSession) -> None:
    # Flushed, never committed: the row disappears with the test's rollback.
    system_db.add(
        FeatureFlag(
            key=str(FlagKey.PRODUCT_EVENTS_ENABLED),
            scope=FlagScope.GLOBAL,
            tenant_id=None,
            enabled=True,
            rollout_percentage=100,
        )
    )
    await system_db.flush()


async def _rows(system_db: AsyncSession, tenant_id: uuid.UUID) -> list[ProductEvent]:
    statement = sa.select(ProductEvent).where(ProductEvent.tenant_id == tenant_id)
    return list((await system_db.execute(statement)).scalars().all())


class TestRecording:
    async def test_flag_off_records_nothing(self, system_db: AsyncSession) -> None:
        tenant_id = await _tenant(system_db)
        service = ProductEventService(system_db)

        assert await service.record(ProductEventName.FIRST_ORDER, tenant_id=tenant_id) is False
        assert await _rows(system_db, tenant_id) == []

    async def test_first_events_are_recorded_once_per_shop(self, system_db: AsyncSession) -> None:
        await _flag_on(system_db)
        tenant_id = await _tenant(system_db)
        service = ProductEventService(system_db)

        assert await service.record(ProductEventName.FIRST_ORDER, tenant_id=tenant_id) is True
        assert await service.record(ProductEventName.FIRST_ORDER, tenant_id=tenant_id) is False
        # A caller-supplied key cannot make a once-per-shop event repeat.
        assert (
            await service.record(ProductEventName.FIRST_ORDER, tenant_id=tenant_id, dedupe_key="x")
            is False
        )
        rows = await _rows(system_db, tenant_id)
        assert [(r.name, r.dedupe_key) for r in rows] == [("first_order", "once")]

    async def test_every_first_event_and_shop_created_is_once_per_shop(self) -> None:
        expected = {n for n in ProductEventName if n.value.startswith("first_")}
        assert expected | {ProductEventName.SHOP_CREATED} == ONCE_PER_SHOP
        assert ProductEventName.PLAN_PAID not in ONCE_PER_SHOP
        assert ProductEventName.WEEKLY_ACTIVE not in ONCE_PER_SHOP

    async def test_plan_paid_needs_a_payment_key_and_counts_each_payment(
        self, system_db: AsyncSession
    ) -> None:
        await _flag_on(system_db)
        tenant_id = await _tenant(system_db)
        service = ProductEventService(system_db)

        assert await service.record(ProductEventName.PLAN_PAID, tenant_id=tenant_id) is False
        for key in ("txn_a", "txn_b", "txn_a"):
            await service.record(ProductEventName.PLAN_PAID, tenant_id=tenant_id, dedupe_key=key)
        assert sorted(r.dedupe_key for r in await _rows(system_db, tenant_id)) == [
            "txn_a",
            "txn_b",
        ]

    async def test_weekly_active_is_once_per_iso_week(self, system_db: AsyncSession) -> None:
        await _flag_on(system_db)
        tenant_id = await _tenant(system_db)
        service = ProductEventService(system_db)

        assert await service.weekly_active(tenant_id=tenant_id, user_id=None) is True
        assert await service.weekly_active(tenant_id=tenant_id, user_id=None) is False
        assert iso_week_key(date(2026, 11, 17)) == "2026-W47"
        # ISO years: 2027-01-01 is a Friday in week 53 of 2026.
        assert iso_week_key(date(2027, 1, 1)) == "2026-W53"

    async def test_shop_created_is_recorded_before_any_tenant_is_in_scope(
        self, db: AsyncSession
    ) -> None:
        # ``db`` is a guarded session with no tenant in scope, as during shop creation.
        from app.tenants.models import Tenant

        with use_context(tenant_id=None):
            tenant = Tenant(name="Scoped Funnel Shop", business_category="CLOTHING")
            db.add(tenant)
            await db.flush()
        db.add(
            FeatureFlag(
                key=str(FlagKey.PRODUCT_EVENTS_ENABLED),
                scope=FlagScope.GLOBAL,
                tenant_id=None,
                enabled=True,
                rollout_percentage=100,
            )
        )
        await db.flush()

        recorded = await ProductEventService(db).record(
            ProductEventName.SHOP_CREATED, tenant_id=tenant.id
        )

        assert recorded is True

    async def test_event_for_another_tenant_is_refused(self, system_db: AsyncSession) -> None:
        await _flag_on(system_db)
        mine = await _tenant(system_db, "Mine")
        theirs = await _tenant(system_db, "Theirs")

        with use_context(tenant_id=mine):
            recorded = await ProductEventService(system_db).record(
                ProductEventName.FIRST_ORDER, tenant_id=theirs
            )

        assert recorded is False
        assert await _rows(system_db, theirs) == []

    async def test_a_lost_race_does_not_break_the_callers_transaction(
        self, system_db: AsyncSession
    ) -> None:
        await _flag_on(system_db)
        tenant_id = await _tenant(system_db)
        service = ProductEventService(system_db)
        await service.record(ProductEventName.FIRST_BOOKING, tenant_id=tenant_id)

        # Simulate a concurrent writer: the existence check misses the row, so
        # the database's unique constraint is what refuses the duplicate.
        with patch.object(ProductEventService, "_exists", return_value=False):
            again = await service.record(ProductEventName.FIRST_BOOKING, tenant_id=tenant_id)

        assert again is False
        # The caller's transaction is intact and can keep working.
        assert len(await _rows(system_db, tenant_id)) == 1
        await _tenant(system_db, "Still Works")

    async def test_unexpected_errors_never_reach_the_caller(self, system_db: AsyncSession) -> None:
        tenant_id = await _tenant(system_db)
        with patch.object(ProductEventService, "_record", side_effect=RuntimeError("boom")):
            assert (
                await ProductEventService(system_db).record(
                    ProductEventName.FIRST_ORDER, tenant_id=tenant_id
                )
                is False
            )


class TestNoPersonalData:
    def test_props_keep_only_counts_flags_and_codes(self) -> None:
        cleaned = clean_props(
            {
                "provider": "STEADFAST",
                "plan": "pro",
                "items": 3,
                "first_try": True,
                "phone": "01700000001",
                "phone_plus": "+8801700000001",
                "customer": "Rahim Uddin",
                "note": "please call before delivery",
                "ref": "ORD12345678",
                "Bad-Key": "X",
                "amount": 12.5,
            }
        )
        assert cleaned == {"provider": "STEADFAST", "plan": "pro", "items": 3, "first_try": True}

    async def test_stored_props_are_cleaned(self, system_db: AsyncSession) -> None:
        await _flag_on(system_db)
        tenant_id = await _tenant(system_db)
        await ProductEventService(system_db).record(
            ProductEventName.FIRST_ORDER,
            tenant_id=tenant_id,
            props={"source": "PASTE", "phone": "01700000002", "text": "আমার অর্ডার"},
        )
        (row,) = await _rows(system_db, tenant_id)
        assert row.props == {"source": "PASTE"}


class TestFunnel:
    async def test_counts_distinct_shops_per_step_inside_the_window(
        self, system_db: AsyncSession
    ) -> None:
        first = await _tenant(system_db, "A")
        second = await _tenant(system_db, "B")
        inside = datetime(2001, 3, 10, 6, 0, tzinfo=UTC)
        outside = datetime(2001, 4, 10, 6, 0, tzinfo=UTC)
        for tenant_id, name, key, at in [
            (first, "shop_created", "once", inside),
            (second, "shop_created", "once", inside),
            (first, "first_order", "once", inside),
            (first, "plan_paid", "p1", inside),
            (first, "plan_paid", "p2", inside),
            (second, "first_order", "once", outside),
        ]:
            system_db.add(
                ProductEvent(tenant_id=tenant_id, name=name, dedupe_key=key, occurred_at=at)
            )
        await system_db.flush()

        steps = await ProductEventService(system_db).funnel(
            start=date(2001, 3, 1), end=date(2001, 3, 31)
        )

        counts = {step.name: step.shops for step in steps}
        assert [step.name for step in steps] == [str(s) for s in FUNNEL_STEPS]
        assert counts["shop_created"] == 2
        assert counts["first_order"] == 1
        assert counts["plan_paid"] == 1  # shops, not payments
        assert counts["first_booking"] == 0

    def test_support_can_read_the_funnel(self) -> None:
        assert AdminPermission.OPS_READ in admin_permissions_for(PlatformAdminRole.SUPPORT)


@pytest.mark.usefixtures("admin_client")
class TestFunnelEndpoint:
    async def test_requires_an_admin_token(self, client: AsyncClient) -> None:
        response = await client.get("/v1/admin/ops/funnel?from=2001-01-01&to=2001-01-31")
        assert response.status_code == 401

    async def test_returns_every_step_in_order(self, client: AsyncClient) -> None:
        response = await client.get(
            "/v1/admin/ops/funnel?from=2001-01-01&to=2001-01-31", headers=admin_header()
        )
        assert response.status_code == 200
        body: dict[str, Any] = response.json()
        assert body["from"] == "2001-01-01"
        assert body["to"] == "2001-01-31"
        assert [s["name"] for s in body["steps"]] == [str(s) for s in FUNNEL_STEPS]

    @pytest.mark.parametrize(
        "query", ["from=2001-02-01&to=2001-01-01", "from=2001-01-01&to=2002-06-01", "from=x&to=y"]
    )
    async def test_rejects_a_bad_window(self, client: AsyncClient, query: str) -> None:
        response = await client.get(f"/v1/admin/ops/funnel?{query}", headers=admin_header())
        assert response.status_code in (400, 422)
