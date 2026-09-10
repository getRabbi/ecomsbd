"""Tenant isolation.

Master spec section 47: *tenant A can never retrieve tenant B's resource by id.*
Section 117 lists cross-tenant data exposure as a P0 incident.

Each of the four guards in :mod:`app.db.tenancy` is tested directly, plus the
HTTP path, because a guard that only works in a unit test is not protection.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.idempotency import IdempotencyKey
from app.core.clock import utc_now
from app.core.context import RequestContext, set_context
from app.core.errors import TenantIsolationError
from app.db.tenancy import allow_cross_tenant, tenant_owned_table_names
from app.entitlements.catalog import PlanCode
from app.entitlements.models import Subscription
from app.tenants.models import Tenant, TenantUser
from app.tenants.roles import TenantRole
from app.users.models import User


def _use_tenant(tenant_id: uuid.UUID | None, user_id: uuid.UUID | None = None) -> None:
    set_context(RequestContext(trace_id="test", tenant_id=tenant_id, user_id=user_id))


@pytest_asyncio.fixture
async def two_tenants(system_db: AsyncSession) -> AsyncIterator[dict[str, uuid.UUID]]:
    """Two shops, each with an owner and one tenant-owned row."""
    users = [
        User(
            phone_search_hmac=f"hmac-{uuid.uuid4().hex}",
            phone_enc="enc",
            phone_last4="0000",
        )
        for _ in range(2)
    ]
    system_db.add_all(users)
    await system_db.flush()

    tenants = [Tenant(name="Shop A"), Tenant(name="Shop B")]
    system_db.add_all(tenants)
    await system_db.flush()

    for tenant, user in zip(tenants, users, strict=True):
        system_db.add(TenantUser(tenant_id=tenant.id, user_id=user.id, role=TenantRole.OWNER))
        system_db.add(Subscription(tenant_id=tenant.id, plan_code=PlanCode.FREE, status="ACTIVE"))
    await system_db.commit()

    # Each test gets freshly generated tenants, and the database file is
    # discarded at the end of the session, so the rows are left in place rather
    # than deleted: a teardown DELETE would contend with the still-open
    # test session and deadlock on SQLite for no benefit.
    yield {
        "tenant_a": tenants[0].id,
        "tenant_b": tenants[1].id,
        "user_a": users[0].id,
        "user_b": users[1].id,
    }


class TestReadIsolation:
    async def test_list_returns_only_the_active_tenants_rows(
        self, db: AsyncSession, two_tenants: dict[str, uuid.UUID]
    ) -> None:
        _use_tenant(two_tenants["tenant_a"])
        rows = (await db.execute(sa.select(TenantUser))).scalars().all()
        assert rows, "tenant A should see its own membership"
        assert all(row.tenant_id == two_tenants["tenant_a"] for row in rows)

    async def test_tenant_a_cannot_fetch_tenant_b_by_id(
        self, db: AsyncSession, system_db: AsyncSession, two_tenants: dict[str, uuid.UUID]
    ) -> None:
        # The exact scenario named in master spec section 47.
        b_membership_id = (
            await system_db.execute(
                sa.select(TenantUser.id).where(TenantUser.tenant_id == two_tenants["tenant_b"])
            )
        ).scalar_one()

        _use_tenant(two_tenants["tenant_a"])
        found = (
            await db.execute(sa.select(TenantUser).where(TenantUser.id == b_membership_id))
        ).scalar_one_or_none()
        assert found is None

    async def test_count_is_also_scoped(
        self, db: AsyncSession, two_tenants: dict[str, uuid.UUID]
    ) -> None:
        _use_tenant(two_tenants["tenant_a"])
        count = (
            await db.execute(sa.select(sa.func.count()).select_from(Subscription))
        ).scalar_one()
        assert count == 1

    async def test_no_tenant_in_scope_returns_nothing(
        self, db: AsyncSession, two_tenants: dict[str, uuid.UUID]
    ) -> None:
        # Failing closed: an unauthenticated code path sees no tenant data at all.
        _use_tenant(None)
        rows = (await db.execute(sa.select(Subscription))).scalars().all()
        assert rows == []

    async def test_non_tenant_owned_tables_are_unaffected(
        self, db: AsyncSession, two_tenants: dict[str, uuid.UUID]
    ) -> None:
        _use_tenant(two_tenants["tenant_a"])
        user = await db.get(User, two_tenants["user_b"])
        assert user is not None, "users are not tenant-owned and stay reachable"


class TestWriteIsolation:
    async def test_insert_is_stamped_with_the_active_tenant(
        self, db: AsyncSession, two_tenants: dict[str, uuid.UUID]
    ) -> None:
        _use_tenant(two_tenants["tenant_a"])
        record = IdempotencyKey(
            key=f"k-{uuid.uuid4().hex}",
            endpoint="/v1/test",
            request_sha256="0" * 64,
            expires_at=utc_now(),
        )
        db.add(record)
        await db.flush()
        assert record.tenant_id == two_tenants["tenant_a"]

    async def test_insert_for_another_tenant_is_refused(
        self, db: AsyncSession, two_tenants: dict[str, uuid.UUID]
    ) -> None:
        _use_tenant(two_tenants["tenant_a"])
        db.add(
            IdempotencyKey(
                tenant_id=two_tenants["tenant_b"],
                key=f"k-{uuid.uuid4().hex}",
                endpoint="/v1/test",
                request_sha256="0" * 64,
                expires_at=utc_now(),
            )
        )
        with pytest.raises(TenantIsolationError, match="another tenant"):
            await db.flush()

    async def test_update_of_another_tenants_row_is_refused(
        self, db: AsyncSession, system_db: AsyncSession, two_tenants: dict[str, uuid.UUID]
    ) -> None:
        b_subscription = (
            await system_db.execute(
                sa.select(Subscription).where(Subscription.tenant_id == two_tenants["tenant_b"])
            )
        ).scalar_one()

        _use_tenant(two_tenants["tenant_a"])
        # Attach the foreign row to the scoped session without going through a
        # (correctly filtered) query, to prove the flush guard is independent.
        merged = await db.merge(
            Subscription(
                id=b_subscription.id,
                tenant_id=two_tenants["tenant_b"],
                plan_code=PlanCode.PRO,
                status="ACTIVE",
            )
        )
        merged.plan_code = PlanCode.PRO
        with pytest.raises(TenantIsolationError):
            await db.flush()

    async def test_writing_with_no_tenant_in_scope_is_refused(
        self, db: AsyncSession, two_tenants: dict[str, uuid.UUID]
    ) -> None:
        _use_tenant(None)
        db.add(
            IdempotencyKey(
                key=f"k-{uuid.uuid4().hex}",
                endpoint="/v1/test",
                request_sha256="0" * 64,
                expires_at=utc_now(),
            )
        )
        with pytest.raises(TenantIsolationError, match="no tenant in scope"):
            await db.flush()


class TestRawSqlGuard:
    async def test_core_select_on_a_tenant_table_is_refused(
        self, db: AsyncSession, two_tenants: dict[str, uuid.UUID]
    ) -> None:
        # ORM entities are filtered automatically; a Core select is not, so it
        # is refused rather than silently returning every tenant's rows.
        _use_tenant(two_tenants["tenant_a"])
        with pytest.raises(TenantIsolationError, match="Core SELECT"):
            await db.execute(sa.select(Subscription.__table__))

    async def test_core_select_may_opt_in_after_filtering_itself(
        self, db: AsyncSession, two_tenants: dict[str, uuid.UUID]
    ) -> None:
        from app.db.tenancy import TENANT_CHECKED

        _use_tenant(two_tenants["tenant_a"])
        table = Subscription.__table__
        rows = (
            await db.execute(
                sa.select(table).where(table.c.tenant_id == two_tenants["tenant_a"]),
                execution_options={TENANT_CHECKED: True},
            )
        ).all()
        assert len(rows) == 1


class TestEscapeHatches:
    async def test_system_session_reads_across_tenants(
        self, system_db: AsyncSession, two_tenants: dict[str, uuid.UUID]
    ) -> None:
        _use_tenant(two_tenants["tenant_a"])
        rows = (await system_db.execute(sa.select(Subscription))).scalars().all()
        assert len({row.tenant_id for row in rows}) >= 2

    async def test_explicit_bypass_reads_across_tenants(
        self, db: AsyncSession, two_tenants: dict[str, uuid.UUID]
    ) -> None:
        _use_tenant(two_tenants["tenant_a"])
        with allow_cross_tenant("test: deliberate cross-tenant read"):
            rows = (await db.execute(sa.select(Subscription))).scalars().all()
        assert len({row.tenant_id for row in rows}) >= 2

    async def test_bypass_does_not_leak_past_its_block(
        self, db: AsyncSession, two_tenants: dict[str, uuid.UUID]
    ) -> None:
        _use_tenant(two_tenants["tenant_a"])
        with allow_cross_tenant("test"):
            pass
        rows = (await db.execute(sa.select(Subscription))).scalars().all()
        assert {row.tenant_id for row in rows} == {two_tenants["tenant_a"]}


class TestRegistry:
    def test_every_tenant_owned_model_is_discovered(self) -> None:
        # The guard keys off this set, so a new tenant-owned table is protected
        # the moment it is declared rather than when someone remembers to list it.
        names = tenant_owned_table_names()
        assert {"tenant_users", "subscriptions", "idempotency_keys"} <= names
        assert "users" not in names
        assert "tenants" not in names


class TestCommerceCoreIsolation:
    """The Phase B tables, checked individually.

    The registry test above proves the guard *knows about* them. These prove the
    guard actually holds for the tables that carry a shop's stock, customers and
    money — the ones where a leak would be the section 117 P0 incident rather
    than a curiosity.
    """

    async def test_every_phase_b_table_is_tenant_owned(self) -> None:
        names = tenant_owned_table_names()
        assert {
            "products",
            "stock_movements",
            "customers",
            "customer_addresses",
            "orders",
            "order_items",
            "consignments",
            "consignment_items",
            "imports",
            "import_rows",
            "sync_mutations",
        } <= names

    async def test_products_are_not_visible_across_shops(
        self, db: AsyncSession, two_tenants: dict[str, uuid.UUID]
    ) -> None:
        from app.products.models import Product

        _use_tenant(two_tenants["tenant_a"])
        db.add(Product(name="Cotton Abaya", cost_paisa=40000))
        await db.commit()

        _use_tenant(two_tenants["tenant_b"])
        rows = (await db.execute(sa.select(Product))).scalars().all()
        assert rows == []

    async def test_another_shops_product_cannot_be_fetched_by_id(
        self, db: AsyncSession, two_tenants: dict[str, uuid.UUID]
    ) -> None:
        from app.products.models import Product

        _use_tenant(two_tenants["tenant_a"])
        product = Product(name="Silk Hijab", cost_paisa=30000)
        db.add(product)
        await db.commit()
        product_id = product.id

        _use_tenant(two_tenants["tenant_b"])
        db.expunge_all()
        # By id is the dangerous case: an id that leaked into a URL must still
        # resolve to nothing for the wrong shop (section 47).
        found = (
            await db.execute(sa.select(Product).where(Product.id == product_id))
        ).scalar_one_or_none()
        assert found is None

    async def test_customers_are_not_visible_across_shops(
        self, db: AsyncSession, two_tenants: dict[str, uuid.UUID]
    ) -> None:
        from app.customers.models import Customer

        _use_tenant(two_tenants["tenant_a"])
        db.add(
            Customer(
                phone_enc="enc",
                phone_search_hmac=f"hmac-{uuid.uuid4().hex}",
                phone_last4="5678",
                phone_masked="01712****78",
                addresses=[],
            )
        )
        await db.commit()

        _use_tenant(two_tenants["tenant_b"])
        rows = (await db.execute(sa.select(Customer))).scalars().all()
        assert rows == []

    async def test_orders_are_not_visible_across_shops(
        self, db: AsyncSession, two_tenants: dict[str, uuid.UUID]
    ) -> None:
        from app.orders.models import Order

        _use_tenant(two_tenants["tenant_a"])
        db.add(
            Order(
                order_number="CP-20260910-0001",
                client_id=uuid.uuid4(),
                business_date=utc_now().date(),
                cod_amount_paisa=125000,
            )
        )
        await db.commit()

        _use_tenant(two_tenants["tenant_b"])
        rows = (await db.execute(sa.select(Order))).scalars().all()
        assert rows == []

    async def test_writing_an_order_for_another_shop_is_refused(
        self, db: AsyncSession, two_tenants: dict[str, uuid.UUID]
    ) -> None:
        from app.orders.models import Order

        _use_tenant(two_tenants["tenant_a"])
        db.add(
            Order(
                tenant_id=two_tenants["tenant_b"],
                order_number="CP-20260910-0002",
                client_id=uuid.uuid4(),
                business_date=utc_now().date(),
                cod_amount_paisa=125000,
            )
        )
        with pytest.raises(TenantIsolationError):
            await db.flush()
        await db.rollback()

    async def test_stock_movements_are_not_visible_across_shops(
        self, db: AsyncSession, two_tenants: dict[str, uuid.UUID]
    ) -> None:
        from app.products.models import (
            Product,
            StockMovement,
            StockMovementReason,
            StockMovementSource,
        )

        _use_tenant(two_tenants["tenant_a"])
        product = Product(name="Cotton Abaya", cost_paisa=40000)
        db.add(product)
        await db.flush()
        db.add(
            StockMovement(
                product_id=product.id,
                quantity_delta=10,
                balance_after=10,
                reason=StockMovementReason.OPENING,
                source=StockMovementSource.SELLER,
                occurred_at=utc_now(),
            )
        )
        await db.commit()

        _use_tenant(two_tenants["tenant_b"])
        # The ledger is the one table where a leak would also be a money leak.
        rows = (await db.execute(sa.select(StockMovement))).scalars().all()
        assert rows == []

    async def test_sync_mutations_are_not_visible_across_shops(
        self, db: AsyncSession, two_tenants: dict[str, uuid.UUID]
    ) -> None:
        from app.sync.models import SyncMutation

        _use_tenant(two_tenants["tenant_a"])
        db.add(
            SyncMutation(
                mutation_id=uuid.uuid4(),
                entity_type="ORDER",
                entity_id=uuid.uuid4(),
                operation="CREATE",
                status="APPLIED",
                payload={},
            )
        )
        await db.commit()

        _use_tenant(two_tenants["tenant_b"])
        rows = (await db.execute(sa.select(SyncMutation))).scalars().all()
        assert rows == []


class TestMoneyCoreIsolation:
    """The Phase D tables.

    The stakes are higher here than anywhere else in the schema: a leak in the
    ledger or the receivables would not just expose data, it would let one
    shop's money appear in another's balance. Each table gets its own
    cross-tenant read attempt.
    """

    async def test_every_phase_d_table_is_tenant_owned(self) -> None:
        names = tenant_owned_table_names()
        assert {
            "financial_ledger_entries",
            "cod_receivables",
            "payouts",
            "payout_lines",
            "payout_source_files",
            "payout_adjustments",
            "reconciliation_cases",
        } <= names

    async def test_ledger_entries_are_not_visible_across_shops(
        self, db: AsyncSession, two_tenants: dict[str, uuid.UUID]
    ) -> None:
        from app.ledger.models import LedgerEntry

        _use_tenant(two_tenants["tenant_a"])
        db.add(
            LedgerEntry(
                occurred_at=utc_now(),
                business_date=utc_now().date(),
                entity_type="consignment",
                entity_id=uuid.uuid4(),
                event_type="DELIVERY_CONFIRMED",
                amount_paisa=140_500,
                direction="CREDIT",
                bucket="COD_RECEIVABLE",
                source="PROVIDER_EVENT",
                created_at=utc_now(),
            )
        )
        await db.commit()

        _use_tenant(two_tenants["tenant_b"])
        rows = (await db.execute(sa.select(LedgerEntry))).scalars().all()
        assert rows == []

    async def test_a_ledger_entry_cannot_be_written_for_another_shop(
        self, db: AsyncSession, two_tenants: dict[str, uuid.UUID]
    ) -> None:
        from app.ledger.models import LedgerEntry

        _use_tenant(two_tenants["tenant_a"])
        db.add(
            LedgerEntry(
                tenant_id=two_tenants["tenant_b"],
                occurred_at=utc_now(),
                business_date=utc_now().date(),
                entity_type="consignment",
                entity_id=uuid.uuid4(),
                event_type="DELIVERY_CONFIRMED",
                amount_paisa=140_500,
                direction="CREDIT",
                bucket="COD_RECEIVABLE",
                source="PROVIDER_EVENT",
                created_at=utc_now(),
            )
        )
        with pytest.raises(TenantIsolationError):
            await db.flush()
        await db.rollback()

    async def test_payouts_are_not_visible_across_shops(
        self, db: AsyncSession, two_tenants: dict[str, uuid.UUID]
    ) -> None:
        from app.payouts.models import Payout

        _use_tenant(two_tenants["tenant_a"])
        db.add(
            Payout(
                provider="manual",
                source="MANUAL",
                status="RECEIVED",
                total_paisa=480_500,
                received_at=utc_now(),
            )
        )
        await db.commit()

        _use_tenant(two_tenants["tenant_b"])
        rows = (await db.execute(sa.select(Payout))).scalars().all()
        assert rows == []

    async def test_payout_source_files_are_not_visible_across_shops(
        self, db: AsyncSession, two_tenants: dict[str, uuid.UUID]
    ) -> None:
        from app.payouts.models import PayoutSourceFile

        _use_tenant(two_tenants["tenant_a"])
        db.add(
            PayoutSourceFile(
                provider="manual",
                original_filename="sept.csv",
                sha256="a" * 64,
                size_bytes=120,
                raw_content="Invoice,Amount\nCP-1,1405\n",
                imported_at=utc_now(),
                created_at=utc_now(),
            )
        )
        await db.commit()

        # The statement holds another shop's customers and amounts; it is the
        # single most sensitive row in the money core.
        _use_tenant(two_tenants["tenant_b"])
        rows = (await db.execute(sa.select(PayoutSourceFile))).scalars().all()
        assert rows == []

    async def test_reconciliation_cases_are_not_visible_across_shops(
        self, db: AsyncSession, two_tenants: dict[str, uuid.UUID]
    ) -> None:
        from app.reconciliation.models import ReconciliationCase

        _use_tenant(two_tenants["tenant_a"])
        db.add(
            ReconciliationCase(
                kind="DELIVERED_BUT_UNPAID",
                status="OPEN",
                priority="HIGH",
                subject_type="cod_receivable",
                subject_id=uuid.uuid4(),
                amount_paisa=140_500,
                summary="CP-1 was delivered and has not been paid",
                opened_at=utc_now(),
            )
        )
        await db.commit()

        _use_tenant(two_tenants["tenant_b"])
        rows = (await db.execute(sa.select(ReconciliationCase))).scalars().all()
        assert rows == []


class TestProfitAndAlertsIsolation:
    """The Phase E tables.

    Profit is the number the seller trusts most, and the notification centre is
    where the shop's worst news is written in plain language. A leak in either
    is a section 117 P0 incident.
    """

    async def test_every_phase_e_table_is_tenant_owned(self) -> None:
        names = tenant_owned_table_names()
        assert {
            "consignment_charges",
            "profit_snapshots",
            "expenses",
            "expense_allocations",
            "notifications",
        } <= names

    async def test_profit_snapshots_are_not_visible_across_shops(
        self, db: AsyncSession, two_tenants: dict[str, uuid.UUID]
    ) -> None:
        from app.consignments.models import Consignment
        from app.core.clock import business_date
        from app.orders.models import Order
        from app.profit.models import ProfitSnapshot

        _use_tenant(two_tenants["tenant_a"])

        # A real order and consignment, not two random UUIDs. PostgreSQL
        # enforces the foreign keys that SQLite does not, so a snapshot
        # pointing at a parcel that does not exist is rejected there — and a
        # fixture that only works on the permissive backend is a test that
        # proves less than it looks like it does.
        order = Order(
            order_number="CP-20260910-9001",
            client_id=uuid.uuid4(),
            business_date=business_date(at=utc_now()),
            cod_amount_paisa=140_500,
        )
        db.add(order)
        await db.flush()

        consignment = Consignment(
            order_id=order.id,
            merchant_reference=order.order_number,
            cod_amount_paisa=140_500,
        )
        db.add(consignment)
        await db.flush()

        db.add(
            ProfitSnapshot(
                consignment_id=consignment.id,
                order_id=order.id,
                revision=1,
                is_current=True,
                realized_revenue_paisa=140_500,
                contribution_profit_paisa=52_300,
                quality="ACTUAL",
                outcome="DELIVERED",
                business_date=business_date(at=utc_now()),
                calculated_at=utc_now(),
            )
        )
        await db.commit()

        _use_tenant(two_tenants["tenant_b"])
        rows = (await db.execute(sa.select(ProfitSnapshot))).scalars().all()
        assert rows == []

    async def test_expenses_are_not_visible_across_shops(
        self, db: AsyncSession, two_tenants: dict[str, uuid.UUID]
    ) -> None:
        from app.core.clock import business_date
        from app.expenses.models import Expense

        today = business_date(at=utc_now())
        _use_tenant(two_tenants["tenant_a"])
        db.add(
            Expense(
                kind="AD_SPEND",
                amount_paisa=500_000,
                period_start=today,
                period_end=today,
                description="Facebook boost",
                preferred_method="EQUAL_PER_DELIVERED_ORDER",
            )
        )
        await db.commit()

        # What a competitor spends on ads is exactly the kind of thing that
        # must never cross shops.
        _use_tenant(two_tenants["tenant_b"])
        rows = (await db.execute(sa.select(Expense))).scalars().all()
        assert rows == []

    async def test_notifications_are_not_visible_across_shops(
        self, db: AsyncSession, two_tenants: dict[str, uuid.UUID]
    ) -> None:
        from app.core.clock import business_date
        from app.notifications.models import Notification

        _use_tenant(two_tenants["tenant_a"])
        db.add(
            Notification(
                kind="DELIVERED_BUT_UNPAID",
                severity="CRITICAL",
                title="Delivered but not paid",
                body="7 parcels reached the customer and the money has not arrived.",
                dedupe_key="DELIVERED_BUT_UNPAID:2026-09-10",
                amount_paisa=895_000,
                item_count=7,
                business_date=business_date(at=utc_now()),
                created_at=utc_now(),
            )
        )
        await db.commit()

        _use_tenant(two_tenants["tenant_b"])
        rows = (await db.execute(sa.select(Notification))).scalars().all()
        assert rows == []
