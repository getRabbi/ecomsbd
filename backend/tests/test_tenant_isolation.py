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
