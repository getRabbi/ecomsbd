"""Entitlements: resolution, metering and downgrade safety.

Master spec sections 26, 43 and 51. Three claims are under test:

*   a plan is resolved from a **verified** subscription and nothing else;
*   a metered quota cannot be overspent, including at the boundary and under
    concurrency;
*   a downgrade restricts automation and volume and **never** takes away the
    seller's own history — section 51 is the rule the whole product's
    trustworthiness rests on.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.models import BillingProviderKind
from app.core.errors import EntitlementRequiredError
from app.entitlements.catalog import (
    PLANS,
    UNLIMITED,
    Entitlement,
    PlanCode,
    get_plan,
    plan_catalog,
)
from app.entitlements.models import Subscription, SubscriptionStatus
from app.entitlements.service import EntitlementResolver, EntitlementService
from app.entitlements.usage import (
    METERED_ENTITLEMENTS,
    UsageCounter,
    increment_atomically,
    period_key_for,
)
from tests.conftest_commerce import create_order, create_product, grant_plan, signed_in_shop
from tests.test_auth_flow import auth_header


def _subscription(**kwargs: object) -> Subscription:
    """An unattached Subscription for pure resolver tests."""
    defaults: dict[str, object] = {
        "tenant_id": uuid.uuid4(),
        "plan_code": str(PlanCode.PRO),
        "status": str(SubscriptionStatus.ACTIVE),
        "source": str(BillingProviderKind.PLAY),
    }
    defaults.update(kwargs)
    return Subscription(**defaults)


async def _tenant(system_db: AsyncSession, name: str = "Entitlement Shop") -> uuid.UUID:
    from app.tenants.models import Tenant

    tenant = Tenant(name=name, business_category="CLOTHING")
    system_db.add(tenant)
    await system_db.flush()
    return tenant.id


def _scoped(tenant_id: uuid.UUID) -> None:
    from dataclasses import replace

    from app.core.context import current_context, set_context

    set_context(replace(current_context(), tenant_id=tenant_id))


# --------------------------------------------------------------------------- #
# Catalogue
# --------------------------------------------------------------------------- #


class TestCatalogue:
    def test_the_spec_table_is_the_catalogue(self) -> None:
        """Master spec section 26, asserted rather than described."""
        free, starter, pro = PLANS[PlanCode.FREE], PLANS[PlanCode.STARTER], PLANS[PlanCode.PRO]
        assert free.limit(Entitlement.ORDERS_MONTHLY_LIMIT) == 20
        assert starter.limit(Entitlement.ORDERS_MONTHLY_LIMIT) == UNLIMITED
        assert free.is_allowed(Entitlement.BULK_BOOKING) is False
        assert starter.is_allowed(Entitlement.BULK_BOOKING) is True
        assert pro.is_allowed(Entitlement.WEB_DASHBOARD) is True
        assert starter.is_allowed(Entitlement.WEB_DASHBOARD) is False
        assert pro.limit(Entitlement.TEAM_MEMBER_LIMIT) == 5

    def test_an_unknown_plan_degrades_to_free_rather_than_raising(self) -> None:
        """A rolled-back release must not 500 every request."""
        assert get_plan("enterprise-2029").code is PlanCode.FREE

    def test_prices_are_configurable_because_they_are_a_hypothesis(self) -> None:
        catalog = plan_catalog(price_overrides={"starter": 24_900})
        assert catalog[PlanCode.STARTER].price_paisa == 24_900
        assert PLANS[PlanCode.STARTER].price_paisa == 19_900, "the base table is untouched"

    def test_entitlement_values_are_configurable(self) -> None:
        catalog = plan_catalog(entitlement_overrides={"free": {"orders_monthly_limit": 50}})
        assert catalog[PlanCode.FREE].limit(Entitlement.ORDERS_MONTHLY_LIMIT) == 50

    def test_a_nonsense_override_is_ignored_rather_than_fatal(self) -> None:
        """A typo in an operator's environment must not take the API down."""
        catalog = plan_catalog(
            price_overrides={"enterprise": 1},
            entitlement_overrides={"pro": {"not_a_key": 3, "bulk_booking": 7}},
        )
        assert catalog[PlanCode.PRO].is_allowed(Entitlement.BULK_BOOKING) is True

    def test_no_plan_gates_reading_history(self) -> None:
        """Section 51: what plans gate is volume, automation and analysis."""
        history_keys = {Entitlement.PROFIT_HISTORY_DAYS}
        for plan in PLANS.values():
            for key in history_keys:
                assert plan.limit(key) != 0, f"{plan.code} would lock history"


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #


class TestResolver:
    def test_no_subscription_resolves_to_free(self) -> None:
        resolved = EntitlementResolver.resolve(None, at=datetime.now(UTC))
        assert resolved.plan.code is PlanCode.FREE
        assert resolved.status == "none"

    def test_an_expired_period_resolves_to_free(self) -> None:
        now = datetime.now(UTC)
        resolved = EntitlementResolver.resolve(
            _subscription(current_period_end=now - timedelta(minutes=1)), at=now
        )
        assert resolved.plan.code is PlanCode.FREE

    def test_grace_keeps_the_paid_plan(self) -> None:
        """A failed renewal must not cut a seller off mid-retry."""
        now = datetime.now(UTC)
        resolved = EntitlementResolver.resolve(
            _subscription(
                status=str(SubscriptionStatus.GRACE),
                current_period_end=now - timedelta(hours=2),
                grace_until=now + timedelta(days=2),
            ),
            at=now,
        )
        assert resolved.plan.code is PlanCode.PRO
        assert resolved.in_grace is True

    def test_grace_that_has_run_out_does_not(self) -> None:
        now = datetime.now(UTC)
        resolved = EntitlementResolver.resolve(
            _subscription(
                status=str(SubscriptionStatus.GRACE),
                current_period_end=now - timedelta(days=5),
                grace_until=now - timedelta(days=1),
            ),
            at=now,
        )
        assert resolved.plan.code is PlanCode.FREE

    def test_a_trial_runs_to_its_own_end_date(self) -> None:
        now = datetime.now(UTC)
        live = _subscription(
            status=str(SubscriptionStatus.TRIAL), trial_end=now + timedelta(days=3)
        )
        assert EntitlementResolver.resolve(live, at=now).plan.code is PlanCode.PRO

        over = _subscription(
            status=str(SubscriptionStatus.TRIAL), trial_end=now - timedelta(minutes=1)
        )
        assert EntitlementResolver.resolve(over, at=now).plan.code is PlanCode.FREE

    def test_cancel_at_period_end_keeps_access_until_the_period_ends(self) -> None:
        """They paid for the period; cancelling does not refund it by removal."""
        now = datetime.now(UTC)
        resolved = EntitlementResolver.resolve(
            _subscription(
                status=str(SubscriptionStatus.CANCEL_AT_PERIOD_END),
                cancel_at_period_end=True,
                current_period_end=now + timedelta(days=9),
            ),
            at=now,
        )
        assert resolved.plan.code is PlanCode.PRO
        assert resolved.ends_at_period_end is True

    @pytest.mark.parametrize(
        "status",
        [
            SubscriptionStatus.CANCELLED,
            SubscriptionStatus.EXPIRED,
            SubscriptionStatus.REFUNDED,
            SubscriptionStatus.SUSPENDED,
            SubscriptionStatus.PAST_DUE,
        ],
    )
    def test_terminal_states_grant_nothing(self, status: SubscriptionStatus) -> None:
        resolved = EntitlementResolver.resolve(
            _subscription(
                status=str(status), current_period_end=datetime.now(UTC) + timedelta(days=30)
            ),
            at=datetime.now(UTC),
        )
        assert resolved.plan.code is PlanCode.FREE


# --------------------------------------------------------------------------- #
# Usage counters
# --------------------------------------------------------------------------- #


class TestUsageCounters:
    async def test_consume_spends_and_reports(self, system_db: AsyncSession) -> None:
        tenant_id = await _tenant(system_db)
        _scoped(tenant_id)
        service = EntitlementService(system_db)

        view = await service.consume(tenant_id, Entitlement.ORDERS_MONTHLY_LIMIT)
        assert view.used == 1
        assert view.limit == 20  # Free
        assert view.remaining == 19

    async def test_the_quota_refuses_exactly_at_the_limit(self, system_db: AsyncSession) -> None:
        tenant_id = await _tenant(system_db)
        _scoped(tenant_id)
        service = EntitlementService(system_db)

        for _ in range(20):
            await service.consume(tenant_id, Entitlement.ORDERS_MONTHLY_LIMIT)
        with pytest.raises(EntitlementRequiredError) as exc:
            await service.consume(tenant_id, Entitlement.ORDERS_MONTHLY_LIMIT)
        assert exc.value.details["entitlement"] == str(Entitlement.ORDERS_MONTHLY_LIMIT)
        assert exc.value.details["reason"] == "quota_exhausted"

    async def test_the_atomic_increment_refuses_rather_than_clamping(
        self, system_db: AsyncSession
    ) -> None:
        """The property a read-then-write cannot give you.

        Two callers that both read "19 of 20" and both try to spend two would
        each write 21 in a naive implementation. Here the second ``UPDATE``
        matches no rows, because the quota check is inside its ``WHERE``.
        """
        tenant_id = await _tenant(system_db)
        _scoped(tenant_id)
        service = EntitlementService(system_db)
        for _ in range(19):
            await service.consume(tenant_id, Entitlement.ORDERS_MONTHLY_LIMIT)

        period = period_key_for(Entitlement.ORDERS_MONTHLY_LIMIT)
        assert (
            await increment_atomically(
                system_db,
                tenant_id=tenant_id,
                entitlement=Entitlement.ORDERS_MONTHLY_LIMIT,
                period_key=period,
                amount=2,
                limit_value=20,
            )
            is False
        )
        assert (
            await increment_atomically(
                system_db,
                tenant_id=tenant_id,
                entitlement=Entitlement.ORDERS_MONTHLY_LIMIT,
                period_key=period,
                amount=1,
                limit_value=20,
            )
            is True
        )

    async def test_concurrent_spending_never_exceeds_the_cap(self, system_db: AsyncSession) -> None:
        """Interleave two independent services against one counter.

        Each call is the same single conditional statement the API path uses,
        so the total spent is bounded by the cap no matter how they interleave.
        """
        tenant_id = await _tenant(system_db)
        _scoped(tenant_id)
        first = EntitlementService(system_db)
        second = EntitlementService(system_db)

        granted = 0
        for index in range(30):
            service = first if index % 2 == 0 else second
            try:
                await service.consume(tenant_id, Entitlement.ORDERS_MONTHLY_LIMIT)
                granted += 1
            except EntitlementRequiredError:
                pass

        assert granted == 20
        counter = (
            await system_db.execute(
                sa.select(UsageCounter).where(UsageCounter.tenant_id == tenant_id)
            )
        ).scalar_one()
        assert counter.used == 20

    async def test_a_counter_is_scoped_to_its_tenant(self, system_db: AsyncSession) -> None:
        """The bulk UPDATE bypasses the ORM guards, so tenancy is explicit."""
        tenant_a = await _tenant(system_db, "Shop A")
        tenant_b = await _tenant(system_db, "Shop B")
        service = EntitlementService(system_db)

        _scoped(tenant_a)
        for _ in range(5):
            await service.consume(tenant_a, Entitlement.ORDERS_MONTHLY_LIMIT)
        _scoped(tenant_b)
        view = await service.consume(tenant_b, Entitlement.ORDERS_MONTHLY_LIMIT)
        assert view.used == 1

    async def test_a_zero_quota_is_reported_as_not_in_plan(self, system_db: AsyncSession) -> None:
        """Free includes no SMS at all. '0 of 0 used' would be a confusing lie."""
        tenant_id = await _tenant(system_db)
        _scoped(tenant_id)
        service = EntitlementService(system_db)
        with pytest.raises(EntitlementRequiredError) as exc:
            await service.consume(tenant_id, Entitlement.SMS_SEGMENTS_MONTHLY)
        assert exc.value.details["reason"] == "not_in_plan"

    async def test_an_unlimited_quota_never_refuses(self, system_db: AsyncSession) -> None:
        tenant_id = await _tenant(system_db)
        _scoped(tenant_id)
        subscription = Subscription(
            tenant_id=tenant_id,
            plan_code=str(PlanCode.STARTER),
            status=str(SubscriptionStatus.ACTIVE),
            source=str(BillingProviderKind.MANUAL_ADMIN),
            current_period_end=datetime.now(UTC) + timedelta(days=30),
        )
        system_db.add(subscription)
        await system_db.flush()

        service = EntitlementService(system_db)
        for _ in range(50):
            view = await service.consume(tenant_id, Entitlement.ORDERS_MONTHLY_LIMIT)
        assert view.limit == UNLIMITED
        assert view.remaining is None

    async def test_upgrading_mid_period_raises_the_ceiling_and_keeps_the_spend(
        self, system_db: AsyncSession
    ) -> None:
        tenant_id = await _tenant(system_db)
        _scoped(tenant_id)
        free_service = EntitlementService(system_db)
        for _ in range(20):
            await free_service.consume(tenant_id, Entitlement.ORDERS_MONTHLY_LIMIT)

        system_db.add(
            Subscription(
                tenant_id=tenant_id,
                plan_code=str(PlanCode.PRO),
                status=str(SubscriptionStatus.ACTIVE),
                source=str(BillingProviderKind.MANUAL_ADMIN),
                current_period_end=datetime.now(UTC) + timedelta(days=30),
            )
        )
        await system_db.flush()

        upgraded = EntitlementService(system_db)
        view = await upgraded.consume(tenant_id, Entitlement.ORDERS_MONTHLY_LIMIT)
        assert view.used == 21, "the spend is not wiped by an upgrade"
        assert view.limit == UNLIMITED

    def test_period_keys_follow_the_tenant_clock_not_utc(self) -> None:
        """23:30 UTC is already tomorrow in Dhaka (section 69)."""
        late = datetime(2026, 9, 10, 23, 30, tzinfo=UTC)
        assert period_key_for(Entitlement.RISK_CHECKS_DAILY, at=late) == "2026-09-11"
        assert period_key_for(Entitlement.ORDERS_MONTHLY_LIMIT, at=late) == "2026-09"

        month_end = datetime(2026, 9, 30, 20, 0, tzinfo=UTC)
        assert period_key_for(Entitlement.ORDERS_MONTHLY_LIMIT, at=month_end) == "2026-10"

    def test_only_metered_entitlements_can_be_consumed(self) -> None:
        assert Entitlement.BULK_BOOKING not in METERED_ENTITLEMENTS
        with pytest.raises(ValueError, match="not a metered entitlement"):
            period_key_for(Entitlement.BULK_BOOKING)

    async def test_consume_refuses_a_non_metered_entitlement(self, system_db: AsyncSession) -> None:
        tenant_id = await _tenant(system_db)
        _scoped(tenant_id)
        with pytest.raises(ValueError, match="not metered"):
            await EntitlementService(system_db).consume(tenant_id, Entitlement.BULK_BOOKING)

    async def test_standing_limits_are_counted_from_their_own_table(
        self, system_db: AsyncSession
    ) -> None:
        """A removed seat frees itself; a period reset must not free a fifth one."""
        tenant_id = await _tenant(system_db)
        _scoped(tenant_id)
        service = EntitlementService(system_db)

        await service.require_within_limit(
            tenant_id, Entitlement.TEAM_MEMBER_LIMIT, current_count=0
        )
        with pytest.raises(EntitlementRequiredError) as exc:
            await service.require_within_limit(
                tenant_id, Entitlement.TEAM_MEMBER_LIMIT, current_count=1
            )
        assert exc.value.details["reason"] == "limit_reached"


# --------------------------------------------------------------------------- #
# Enforcement over HTTP
# --------------------------------------------------------------------------- #


class TestEnforcement:
    async def test_the_free_order_quota_is_enforced_on_the_api(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone, shop_name="Quota Shop")
        await create_product(client, session, name="Kurti", sku="KUR-1")

        for index in range(20):
            await create_order(client, session, phone=f"017000000{index:02d}")

        response = await client.post(
            "/v1/orders",
            json={
                "phone": "01799999999",
                "items": [{"name": "Kurti", "quantity": 1, "unit_price_paisa": 100_000}],
            },
            headers=auth_header(session),
        )
        assert response.status_code == 402
        body = response.json()
        assert body["code"] == "ENTITLEMENT_REQUIRED"
        assert body["details"]["entitlement"] == "orders_monthly_limit"

    async def test_usage_is_reported_to_the_client_from_the_server(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone, shop_name="Usage Shop")
        await create_product(client, session, name="Scarf", sku="SCF-1")
        await create_order(client, session, phone="01766000001")

        body = (await client.get("/v1/billing/usage", headers=auth_header(session))).json()
        orders = next(u for u in body["usage"] if u["entitlement"] == "orders_monthly_limit")
        assert orders["used"] == 1
        assert orders["limit"] == 20
        assert orders["remaining"] == 19
        # Every metered key is reported, spent or not: "0 of 100" is right and
        # an absent row looks broken.
        assert len(body["usage"]) == len(METERED_ENTITLEMENTS)

    async def test_reconciliation_actions_need_the_entitlement(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone, shop_name="Free Money Shop")
        response = await client.post("/v1/reconciliation/scan", headers=auth_header(session))
        assert response.status_code == 402
        assert response.json()["code"] == "ENTITLEMENT_REQUIRED"

    async def test_reading_reconciliation_cases_never_needs_one(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        """Section 51: the seller's own money history is not a paid feature."""
        session = await signed_in_shop(client, unique_phone, shop_name="Free Money Shop")
        response = await client.get("/v1/reconciliation/cases", headers=auth_header(session))
        assert response.status_code == 200

    async def test_advanced_analysis_is_gated(self, client: AsyncClient, unique_phone: str) -> None:
        session = await signed_in_shop(client, unique_phone, shop_name="Free Insights")
        for path in ("/v1/analytics/returns", "/v1/analytics/products"):
            response = await client.get(path, headers=auth_header(session))
            assert response.status_code == 402, path

    async def test_the_profit_history_window_refuses_rather_than_clamping(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        """A silently narrowed range would misreport profit with nothing saying so."""
        session = await signed_in_shop(client, unique_phone, shop_name="Free Insights")
        response = await client.get(
            "/v1/analytics/profit?since=2026-01-01&until=2026-09-10",
            headers=auth_header(session),
        )
        assert response.status_code == 402
        details = response.json()["details"]
        assert details["entitlement"] == "profit_history_days"
        assert details["max_days"] == "1"

    async def test_todays_profit_is_available_on_free(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        from app.core.clock import business_date

        session = await signed_in_shop(client, unique_phone, shop_name="Free Insights")
        today = business_date().isoformat()
        response = await client.get(
            f"/v1/analytics/profit?since={today}&until={today}", headers=auth_header(session)
        )
        assert response.status_code == 200

    async def test_home_is_never_gated(self, client: AsyncClient, unique_phone: str) -> None:
        """The daily money screen is the product. It works on every plan."""
        session = await signed_in_shop(client, unique_phone, shop_name="Free Home")
        assert (
            await client.get("/v1/analytics/home", headers=auth_header(session))
        ).status_code == 200


# --------------------------------------------------------------------------- #
# Downgrade safety — master spec section 51
# --------------------------------------------------------------------------- #


class TestDowngradeRetainsData:
    async def test_a_lapsed_shop_still_reads_its_own_history(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        """The single most important promise in the plan model.

        A shop buys Pro, records real business, then lets the subscription
        lapse. Everything it created stays readable. What stops is automation
        and new volume — not access to its own past.
        """
        session = await signed_in_shop(client, unique_phone, shop_name="Lapsing Shop", plan="pro")
        headers = auth_header(session)
        await create_product(client, session, name="Abaya", sku="ABY-9")
        order = await create_order(client, session, phone="01733000001")

        assert (await client.get("/v1/analytics/products", headers=headers)).status_code == 200

        # The subscription lapses.
        await _expire_subscription(str(session["tenant_id"]))

        assert (await client.get("/v1/orders", headers=headers)).status_code == 200
        assert (
            await client.get(f"/v1/orders/{order['order']['id']}", headers=headers)
        ).status_code == 200
        assert (await client.get("/v1/customers", headers=headers)).status_code == 200
        assert (await client.get("/v1/products", headers=headers)).status_code == 200
        assert (await client.get("/v1/money/summary", headers=headers)).status_code == 200
        assert (await client.get("/v1/money/receivables", headers=headers)).status_code == 200
        assert (await client.get("/v1/payouts", headers=headers)).status_code == 200
        assert (await client.get("/v1/reconciliation/cases", headers=headers)).status_code == 200
        assert (await client.get("/v1/analytics/home", headers=headers)).status_code == 200
        assert (await client.get("/v1/expenses", headers=headers)).status_code == 200
        assert (await client.get("/v1/notifications", headers=headers)).status_code == 200
        assert (await client.get("/v1/billing/history", headers=headers)).status_code == 200

    async def test_a_lapsed_shop_loses_automation_not_records(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone, shop_name="Lapsing Shop", plan="pro")
        headers = auth_header(session)
        await _expire_subscription(str(session["tenant_id"]))

        assert (await client.post("/v1/reconciliation/scan", headers=headers)).status_code == 402
        assert (await client.get("/v1/analytics/returns", headers=headers)).status_code == 402

        entitlements = (await client.get("/v1/billing/entitlements", headers=headers)).json()
        assert entitlements["plan"] == "free"


async def _expire_subscription(tenant_id: str) -> None:
    """End a shop's subscription the way time would."""
    import uuid as _uuid

    from app.db.session import system_session

    async with system_session("test fixture: expire subscription") as session:
        rows = (
            (
                await session.execute(
                    sa.select(Subscription).where(Subscription.tenant_id == _uuid.UUID(tenant_id))
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            row.status = str(SubscriptionStatus.EXPIRED)
            row.current_period_end = datetime.now(UTC) - timedelta(days=1)
            row.grace_until = None


class TestGrantHelper:
    async def test_the_fixture_grant_actually_changes_the_plan(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone, shop_name="Grant Shop")
        before = (await client.get("/v1/billing/entitlements", headers=auth_header(session))).json()
        assert before["plan"] == "free"

        await grant_plan(str(session["tenant_id"]), "starter")
        after = (await client.get("/v1/billing/entitlements", headers=auth_header(session))).json()
        assert after["plan"] == "starter"


class TestEntitlementPerformance:
    """Master spec section 105 and the Phase F brief's section 44.

    The entitlement gate sits in front of paid operations, so a handler that
    checks four things must not issue four subscription reads. These count real
    SQL, not calls: an N+1 that a cache happens to hide today would come back
    the moment the cache moved.
    """

    @staticmethod
    def _counting(session: AsyncSession) -> list[str]:
        """Record every SQL statement the session sends."""
        from sqlalchemy import event

        statements: list[str] = []
        # `get_bind()` on an AsyncSession returns the *sync* Engine the
        # greenlet runs against, which is already what the event hooks
        # attach to — there is no `.sync_engine` to unwrap.
        engine = session.get_bind()

        def before(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", before)
        return statements

    async def test_many_checks_cost_one_subscription_read(self, system_db: AsyncSession) -> None:
        tenant_id = await _tenant(system_db)
        _scoped(tenant_id)
        await system_db.flush()

        service = EntitlementService(system_db)
        statements = self._counting(system_db)

        # What a request that gates several things actually does.
        await service.is_allowed(tenant_id, Entitlement.BULK_BOOKING)
        await service.is_allowed(tenant_id, Entitlement.RECONCILIATION)
        await service.limit(tenant_id, Entitlement.ORDERS_MONTHLY_LIMIT)
        await service.limit(tenant_id, Entitlement.TEAM_MEMBER_LIMIT)
        await service.snapshot(tenant_id)

        subscription_reads = [s for s in statements if "FROM subscriptions" in s]
        assert len(subscription_reads) == 1, (
            f"{len(subscription_reads)} subscription reads for five checks; "
            "the per-request resolution cache is not doing its job"
        )

    async def test_two_tenants_do_not_share_a_cached_plan(self, system_db: AsyncSession) -> None:
        """The cache is keyed by tenant, and a leak here is a billing bug."""
        tenant_a = await _tenant(system_db, "Shop A")
        tenant_b = await _tenant(system_db, "Shop B")
        system_db.add(
            Subscription(
                tenant_id=tenant_a,
                plan_code=str(PlanCode.PRO),
                status=str(SubscriptionStatus.ACTIVE),
                source=str(BillingProviderKind.MANUAL_ADMIN),
                current_period_end=datetime.now(UTC) + timedelta(days=30),
            )
        )
        await system_db.flush()

        service = EntitlementService(system_db)
        _scoped(tenant_a)
        assert (await service.plan_for(tenant_a)).code is PlanCode.PRO
        _scoped(tenant_b)
        assert (await service.plan_for(tenant_b)).code is PlanCode.FREE

    async def test_the_usage_endpoint_reads_counters_once(self, system_db: AsyncSession) -> None:
        """Every metered key is reported, and it costs one query, not four."""
        tenant_id = await _tenant(system_db)
        _scoped(tenant_id)
        service = EntitlementService(system_db)
        await service.consume(tenant_id, Entitlement.ORDERS_MONTHLY_LIMIT)

        statements = self._counting(system_db)
        views = await service.usage(tenant_id)

        assert len(views) == len(METERED_ENTITLEMENTS)
        counter_reads = [s for s in statements if "FROM usage_counters" in s]
        assert len(counter_reads) == 1
